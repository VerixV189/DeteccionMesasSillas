from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from copy import deepcopy
from shapely.geometry import box

from ..services.optimizador import (
    _build_cluster_template, _find_best_placement, _medir_mesas_promedio, 
    planificar_cluster_para_cliente, _get_geometric_layout, generar_layout_simulado_para_hora
)
from .. import db
from ..models import Mesa, Reserva, Layout

reserva_bp = Blueprint('reserva_bp', __name__)


@reserva_bp.route('/disponibilidad', methods=['GET'])
def get_availability():
    """
    Ruta para el cliente. Muestra la disponibilidad actual y sugiere clusters
    sin modificar el estado. Devuelve un JSON puro y limpio.
    """
    try:
        party_size = int(request.args.get('party_size'))
        reservation_time_str = request.args.get('reservation_time')
        if reservation_time_str:
            target_time = datetime.strptime(reservation_time_str, "%Y-%m-%dT%H:%M")
        else:
            fecha_str = request.args.get('fecha', datetime.now().strftime('%Y-%m-%d'))
            hora_str = request.args.get('hora', datetime.now().strftime('%H:%M'))
            target_time = datetime.strptime(f"{fecha_str} {hora_str}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError) as e:
        return jsonify({"error": f"Parámetros inválidos: {e}"}), 400
    
    # Reutilizar la función de simulación (nombre corregido)
    layout_simulado, error_msg = generar_layout_simulado_para_hora(target_time)
    if error_msg:
        return jsonify({"error": error_msg}), 404

    # El resto de la lógica para sugerir mesas/clusters ya no necesita marcar estados,
    # solo necesita leer el 'estado' que ya hemos establecido.
    success_message = ""
    found_simple_table = False
    for obj_data in layout_simulado['objects'].values():
        is_free = obj_data.get('estado') == 'libre'
        has_capacity = obj_data.get('capacidad_actual', 0) >= party_size
        obj_data['is_available'] = is_free and has_capacity
        obj_data['is_cluster_suggestion'] = False
        if obj_data['is_available']:
            found_simple_table = True
            success_message = "Mesa unica encontrada"

    if not found_simple_table:
        mesas_libres_con_id = [(mid, mdata) for mid, mdata in layout_simulado['objects'].items() if mdata.get('estado') == 'libre']
        
        # Preparar datos para el planificador
        mesas_para_optimizador = []
        for mid, mdata in mesas_libres_con_id:
            mesa_info = deepcopy(mdata)
            mesa_info['id'] = mid
            if mesa_info.get('coords_mesa_metros'):
                mesa_info['geom'] = box(*mesa_info['coords_mesa_metros'])
            mesas_para_optimizador.append(mesa_info)

        cluster_candidate_ids, _ = planificar_cluster_para_cliente(mesas_para_optimizador, party_size)
        
        # Asegurarse de que realmente se encontraron candidatos antes de marcarlos.
        if cluster_candidate_ids:
            success_message = "Cluster de mesas sugerido"
            for table_id in cluster_candidate_ids:
                if table_id in layout_simulado['objects']:
                    layout_simulado['objects'][table_id]['is_cluster_suggestion'] = True

    # 6. Devolver el layout SIMULADO.
    return jsonify({
        "message": success_message,
        "layout": layout_simulado
    })


@reserva_bp.route('/reservar_mesa', methods=['POST'])
def reservar_mesa():
    """
    Ruta para el cliente. Realiza una reserva simple para una mesa específica
    siguiendo la nueva lógica no destructiva.
    """
    try:
        data = request.get_json()
        table_id = data['table_id']
        user_id = data.get('user_id', 'Cliente Anónimo')
        reservation_time_str = data.get('reservation_time')
        if reservation_time_str:
            # Formato: "2025-11-17T22:00"
            target_time = datetime.strptime(reservation_time_str, "%Y-%m-%dT%H:%M")
        else:
            fecha_str = data.get('fecha', datetime.now().strftime('%Y-%m-%d'))
            hora_str = data.get('hora', datetime.now().strftime('%H:%M'))
            target_time = datetime.strptime(f"{fecha_str} {hora_str}", "%Y-%m-%d %H:%M")

    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"Petición inválida. Se requiere 'table_id' y una fecha/hora. Error: {e}"}), 400

    # 1. Verificar que la mesa sigue disponible EN ESE MOMENTO
    RESERVATION_DURATION = timedelta(minutes=120)
    target_end_time = target_time + RESERVATION_DURATION
    earliest_start_time = target_time - RESERVATION_DURATION

    reservas_conflictivas = db.session.query(Reserva).join(Reserva.mesas).filter(
        Mesa.id_str == table_id,
        Reserva.status == 'activa',
        Reserva.reservation_time < target_end_time,
        Reserva.reservation_time > earliest_start_time
    ).count()

    if reservas_conflictivas > 0:
        return jsonify({"error": f"Lo sentimos, la mesa '{table_id}' ya no está disponible para esa hora."}), 409

    mesa_a_reservar = Mesa.query.filter_by(id_str=table_id).first()
    if not mesa_a_reservar:
        return jsonify({"error": f"La mesa '{table_id}' no existe."}), 404

    # 3. Crear la reserva SIN plan de movimiento y asociarla a la mesa.
    try:
        nueva_reserva = Reserva(
            user_id=user_id,
            num_people=mesa_a_reservar.capacidad_actual,
            reservation_time=target_time,
            movimiento_info_json=None,
            layout_id=mesa_a_reservar.layout_id
        )
        nueva_reserva.mesas.append(mesa_a_reservar)
        db.session.add(nueva_reserva)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error al guardar la reserva: {e}"}), 500

    # Generar y devolver el layout actualizado (nombre corregido)
    layout_actualizado, error_msg = generar_layout_simulado_para_hora(target_time)
    if error_msg:
        return jsonify({"error": error_msg}), 500

    return jsonify({
        "message": f"Mesa {table_id} reservada con éxito.",
        "layout": layout_actualizado
    })


@reserva_bp.route('/reservar_cluster', methods=['POST'])
def reservar_cluster():
    """
    Endpoint para que un cliente reserve un CLÚSTER ESPECÍFICO que le fue sugerido.
    Refactorizado para ser más seguro y consistente.
    """
    try:
        data = request.get_json()
        table_ids = data['table_ids']
        num_people = int(data['num_people'])
        user_id = data.get('user_id', 'Cliente Anónimo')
        reservation_time_str = data.get('reservation_time')
        if reservation_time_str:
            target_time = datetime.strptime(reservation_time_str, "%Y-%m-%dT%H:%M")
        else:
            return jsonify({"error": "Falta el parámetro 'reservation_time'."}), 400
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Petición inválida."}), 400

    # 1. Verificar que las mesas no tengan conflictos de horario.
    RESERVATION_DURATION = timedelta(minutes=120)
    target_end_time = target_time + RESERVATION_DURATION
    earliest_start_time = target_time - RESERVATION_DURATION

    reservas_conflictivas = db.session.query(Reserva).join(Reserva.mesas).filter(
        Mesa.id_str.in_(table_ids),
        Reserva.status == 'activa',
        Reserva.reservation_time < target_end_time,
        Reserva.reservation_time > earliest_start_time
    ).count()

    if reservas_conflictivas > 0:
        return jsonify({"error": "Lo sentimos, una o más mesas del clúster ya no están disponibles para esa hora."}), 409

    # 2. Simular el estado del restaurante en el momento T usando la función CORRECTA.
    #    Esto nos dará un layout con todos los obstáculos (otras reservas) ya en su sitio.
    layout_simulado, error_msg = generar_layout_simulado_para_hora(target_time)
    if error_msg:
        return jsonify({"error": f"Error al simular el estado del restaurante: {error_msg}"}), 500
    
    # 3. Calcular el plan de movimiento usando el layout simulado.
    #    Ahora 'layout_simulado' contiene los obstáculos en sus posiciones correctas.
    geo_data = _get_geometric_layout(layout_simulado, exclude_ids=table_ids)
    avg_largo, avg_ancho = _medir_mesas_promedio(layout_simulado, table_ids)
    
    sillas_para_movimiento_data = []
    for mesa_id in table_ids:
        sillas_para_movimiento_data.extend(layout_simulado['objects'][mesa_id].get('sillas_asignadas', []))
    sillas_finales_data = sillas_para_movimiento_data[:num_people]
    
    template_h = _build_cluster_template(len(table_ids), avg_ancho, avg_largo, 'horizontal', sillas_finales_data)
    template_v = _build_cluster_template(len(table_ids), avg_ancho, avg_largo, 'vertical', sillas_finales_data)
    
    movimiento_destino = _find_best_placement(template_h, template_v, geo_data["perimetro_geom"], geo_data["obstaculos_geom"])

    if movimiento_destino is None:
        return jsonify({"error": "No se encontró un espacio adecuado para juntar las mesas en el horario solicitado."}), 400

    # 4. Guardar la reserva en la base de datos (lógica sin cambios, pero ahora es segura).
    try:
        mesas_a_asociar = Mesa.query.filter(Mesa.id_str.in_(table_ids)).all()
        if not mesas_a_asociar:
            return jsonify({"error": "No se encontraron las mesas para asociar a la reserva."}), 404

        movimiento_info = {
            'k': len(table_ids), 'mesas_ids': table_ids, 'sillas_ids': [s['id_silla'] for s in sillas_finales_data],
            'destino': { 'centro_x': movimiento_destino['centro'].x, 'centro_y': movimiento_destino['centro'].y, 'orientacion': movimiento_destino['orientacion'] }, 
            'avg_largo_mesa_m': avg_largo, 'avg_ancho_mesa_m': avg_ancho
        }
        
        nueva_reserva = Reserva(
            user_id=user_id, 
            num_people=num_people,
            reservation_time=target_time,
            movimiento_info_json=movimiento_info,
            layout_id=mesas_a_asociar[0].layout_id
        )
        
        mesas_a_asociar = Mesa.query.filter(Mesa.id_str.in_(table_ids)).all()
        for mesa in mesas_a_asociar:
            nueva_reserva.mesas.append(mesa)

        db.session.add(nueva_reserva)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error al aplicar la reserva del clúster en la base de datos: {e}"}), 500

    # Generar y devolver el layout actualizado (nombre corregido)
    layout_actualizado, error_msg = generar_layout_simulado_para_hora(target_time)
    if error_msg:
        return jsonify({"error": error_msg}), 500

    return jsonify({
        "message": "Clúster reservado con éxito.",
        "assigned_tables": table_ids,
        "layout": layout_actualizado
    })

@reserva_bp.route('/usuario/<user_id>', methods=['GET'])
def get_user_reservations(user_id):
    """
    Devuelve todas las reservas de un usuario específico con su estado.
    """
    try:
        # Primero, actualizamos el estado de las reservas que ya pasaron.
        RESERVATION_DURATION = timedelta(minutes=120)
        now = datetime.utcnow()
        
        reservas_pasadas = Reserva.query.filter(
            Reserva.user_id == user_id,
            Reserva.status == 'activa',
            Reserva.reservation_time < (now - RESERVATION_DURATION)
        ).all()

        for r in reservas_pasadas:
            r.status = 'completada'
        
        if reservas_pasadas:
            db.session.commit()

        # Luego, obtenemos todas las reservas del usuario
        reservas_usuario = Reserva.query.filter_by(user_id=user_id).order_by(Reserva.reservation_time.desc()).all()
        
        resultado = [{
            'id': r.id,
            'reservation_time': r.reservation_time.isoformat(),
            'num_people': r.num_people,
            'status': r.status
        } for r in reservas_usuario]
        
        return jsonify({"reservas": resultado})

    except Exception as e:
        return jsonify({"error": f"Error al obtener las reservas: {e}"}), 500


@reserva_bp.route('/<int:reserva_id>', methods=['GET'])
def show_reservation(reserva_id):
    """
    Muestra los detalles de una reserva específica, incluyendo el layout simulado
    correspondiente a la versión del plano en que se hizo la reserva.
    """
    try:
        reserva = Reserva.query.get(reserva_id)
        if not reserva:
            return jsonify({"error": "No se pudo encontrar la reserva solicitada."}), 404

        # Obtener una lista de objetos con el id y la capacidad de cada mesa.
        reserved_tables_info = [
            {'id': mesa.id_str, 'capacity': mesa.capacidad_actual} 
            for mesa in reserva.mesas
        ]

        # --- CAMBIO: Pasar el layout_id de la reserva a la función de simulación ---
        layout_simulado, error_msg = generar_layout_simulado_para_hora(
            reserva.reservation_time, 
            layout_id=reserva.layout_id
        )
        if error_msg:
            return jsonify({"error": error_msg}), 500

        return jsonify({
            'id': reserva.id,
            'reservation_time': reserva.reservation_time.isoformat(),
            'num_people': reserva.num_people,
            'status': reserva.status,
            'tables': reserved_tables_info,
            'layout': layout_simulado
        })

    except Exception as e:
        return jsonify({"error": f"Error al obtener los detalles de la reserva: {e}"}), 500


@reserva_bp.route('/cancelar/<int:reserva_id>', methods=['DELETE'])
def cancel_reservation(reserva_id):
    """
    Cancela una reserva (eliminación lógica).
    """
    try:
        reserva = Reserva.query.get(reserva_id)
        if not reserva:
            return jsonify({"error": "No se pudo encontrar la reserva a cancelar."}), 404

        if reserva.status != 'activa':
            return jsonify({"error": f"La reserva ya está en estado '{reserva.status}' y no puede ser cancelada."}), 409

        if reserva.reservation_time < datetime.utcnow():
             return jsonify({"error": "No se puede cancelar una reserva que ya ha pasado."}), 409

        reserva.status = 'cancelada'
        db.session.commit()
        
        return jsonify({"message": "Reserva cancelada exitosamente."})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Ocurrió un error al intentar cancelar la reserva: {e}"}), 500
