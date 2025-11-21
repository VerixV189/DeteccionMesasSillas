# Contenido de optimizador.py
from shapely.geometry import box, Polygon, MultiPolygon, Point
from shapely.ops import unary_union
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from copy import deepcopy
from shapely.affinity import translate, rotate
import numpy as np
import json
from .. import db
from ..models import Reserva, Layout

RESERVATION_DURATION_MINUTES = 120
CLUSTER_PASS_BUFFER_M = 0.0
# --- CLAVES DEL LAYOUT ---
CAPACITY_KEY = 'capacidad_actual'
STATE_KEY = 'estado'
FREE_STATE = 'libre'
RESERVED_STATE = 'reservado'
TYPE_KEY = 'tipo'
COORDS_M_KEY = 'coords_mesa_metros'
SILLAS_KEY = 'sillas_asignadas'

# --- CONSTANTES DE LÓGICA DE NEGOCIO ---
TIPO_MESA_CUADRADA = 'mesas_cuadradas'
TIPO_MESA_REDONDA = 'mesas_redondas'
SILLA_BUFFER_M = 0.0
MIN_ESPACIO_LIBRE_M = 0.2

def _get_object_footprint(mesa_obj, buffer=0.0):
    """
    Crea una geometría unificada para una mesa y sus sillas asignadas.
    
    """
    from shapely.geometry import box
    from shapely.ops import unary_union
    
    geoms = []
    
    # 1. Añadir la geometría de la mesa
    mesa_coords = mesa_obj.get('coords_mesa_metros')
    if mesa_coords and len(mesa_coords) == 4:
        geoms.append(box(*mesa_coords))
    else:
        print(f"ADVERTENCIA en _get_object_footprint: La mesa {mesa_obj.get('id')} no tiene coordenadas válidas.")
        return None

    # 2. Añadir la geometría de cada silla
    for silla in mesa_obj.get(SILLAS_KEY, []):
        silla_coords = silla.get('coords_metros')
        if silla_coords and len(silla_coords) == 4:
            geoms.append(box(*silla_coords))
        else:
            print(f"ADVERTENCIA en _get_object_footprint: La silla {silla.get('id_silla')} de la mesa {mesa_obj.get('id')} no tiene coordenadas válidas.")
            
    if not geoms:
        return None
        
    unified_geom = unary_union(geoms)
    return unified_geom.buffer(buffer)

def liberar_reservas_expiradas_db():
    """
    Busca en la base de datos reservas que han expirado,
    libera las mesas asociadas y elimina la reserva.
    Esta función está diseñada para ser llamada periódicamente.
    """
    try:
        # Es mejor usar UTC para operaciones de base de datos para evitar problemas de zona horaria.
        now = datetime.utcnow()
        
        # Calcula el punto en el tiempo antes del cual una reserva se considera expirada.
        expiration_threshold = now - timedelta(minutes=RESERVATION_DURATION_MINUTES)

        # 1. Buscar todas las reservas que se crearon antes del umbral de expiración.
        reservas_expiradas = Reserva.query.filter(Reserva.reservation_time < expiration_threshold).all()

        if not reservas_expiradas:
            print("No se encontraron reservas expiradas.")
            return

        print(f"Se encontraron {len(reservas_expiradas)} reservas para liberar.")
        
        for reserva in reservas_expiradas:
            # 2. Para cada reserva expirada, iterar sobre sus mesas asociadas.
            #    La relación 'backref' en el modelo hace esto muy fácil.
            for mesa in reserva.mesas:
                print(f"Liberando mesa: {mesa.id_str}")
                mesa.estado = 'libre'
                mesa.reserva_id = None # Romper la asociación con la reserva
            
            # 3. Eliminar el registro de la reserva de la base de datos.
            db.session.delete(reserva)

        # 4. Confirmar todos los cambios en la base de datos en una sola transacción.
        db.session.commit()

    except Exception as e:
        # Si algo sale mal, revertir los cambios para no dejar la BD en un estado inconsistente.
        db.session.rollback()
        print(f"ERROR durante la liberación de reservas: {e}")

def optimizar_reserva(layout_actual, num_people, user_id, reservation_time):
    """
    Busca la mejor forma de satisfacer una reserva.
    """
    # 1. Obtener mesas libres
    geo_data_inicial = _get_geometric_layout(layout_actual)
    todas_mesas_libres = geo_data_inicial["mesas_libres_cuadradas"] + geo_data_inicial["mesas_libres_redondas"]
    capacidad_total_libre = sum(m.get('capacidad_actual', 0) for m in todas_mesas_libres)
    if capacidad_total_libre < num_people:
        return None, f"Reserva fallida: No hay suficientes sillas libres ({capacidad_total_libre}/{num_people}).", None, None

    # 2. Estrategia 1: Mesa simple
    mesas_ordenadas = sorted(todas_mesas_libres, key=lambda m: m.get('capacidad_actual', 0), reverse=True)
    for mesa in mesas_ordenadas:
        if mesa.get('capacidad_actual', 0) >= num_people:
            mesa_asignada = [mesa['id']]
            return layout_actual, "Reserva simple asignada.", None, mesa_asignada

    # 3. Estrategia 2: Clustering (solo con mesas cuadradas)
    # Se pasa solo la lista de mesas cuadradas a la función de selección.
    mesas_a_mover_ids, k = _seleccionar_mesas_para_cluster(geo_data_inicial["mesas_libres_cuadradas"], num_people)
    if not mesas_a_mover_ids:
        return None, "Reserva fallida: No se pudieron agrupar mesas para la capacidad requerida.", None, None
    
    # 4. Recolectar datos reales de sillas
    sillas_para_movimiento_data = []
    for mesa_id in mesas_a_mover_ids:
        mesa_obj = layout_actual['objects'].get(mesa_id, {})
        sillas_para_movimiento_data.extend(mesa_obj.get('sillas_asignadas', []))
    
    # Asegurarnos de que solo tomamos las sillas necesarias
    sillas_finales_data = sillas_para_movimiento_data[:num_people]
    sillas_finales_ids = [s['id_silla'] for s in sillas_finales_data]

    # 5. Medir mesas y construir obstáculos
    geo_data_final = _get_geometric_layout(layout_actual, exclude_ids=mesas_a_mover_ids)
    perimetro_geom = geo_data_final["perimetro_geom"]
    obstaculos_geom = geo_data_final["obstaculos_geom"]
    avg_largo_mesa_m, avg_ancho_mesa_m = _medir_mesas_promedio(layout_actual, mesas_a_mover_ids)

    # 6. CONSTRUIR LA PLANTILLA EXACTA USANDO DATOS REALES DE SILLAS
    cluster_template_h = _build_cluster_template(k, avg_ancho_mesa_m, avg_largo_mesa_m, 'horizontal', sillas_finales_data)
    cluster_template_v = _build_cluster_template(k, avg_ancho_mesa_m, avg_largo_mesa_m, 'vertical', sillas_finales_data)

    # 7. BUSCAR ESPACIO PARA LA PLANTILLA EXACTA
    movimiento_destino = _find_best_placement(cluster_template_h, cluster_template_v, perimetro_geom, obstaculos_geom)

    if movimiento_destino is None:
        return None, f"Reserva fallida: No se encontró espacio libre para un grupo de {num_people} personas.", None, None

    # 8. Preparar información para la ejecución
    movimiento_info = {
        'k': k,
        'mesas_ids': mesas_a_mover_ids,
        'sillas_ids': sillas_finales_ids, # Pasamos solo los IDs para la ejecución
        'destino': movimiento_destino,
        'avg_largo_mesa_m': avg_largo_mesa_m,
        'avg_ancho_mesa_m': avg_ancho_mesa_m,
    }
    return layout_actual, "Optimización encontrada.", movimiento_info, mesas_a_mover_ids

def _get_geometric_layout(layout_actual, exclude_ids=None):
    """
    Prepara los datos geométricos de forma robusta.
    """
    if exclude_ids is None:
        exclude_ids = []
        
    from shapely.geometry import Polygon, MultiPolygon, box
    from shapely.ops import unary_union

    dims = layout_actual.get('dimensions', {})
    try:
        px_to_m_scale = float(dims.get('width_m')) / float(dims.get('width_px'))
    except (TypeError, ZeroDivisionError):
        px_to_m_scale = 0.05

    perimetro_px = layout_actual.get('perimeter', {}).get('points', [])
    perimetro_m = [[p[0] * px_to_m_scale, p[1] * px_to_m_scale] for p in perimetro_px]
    perimetro_geom = Polygon(perimetro_m) if perimetro_m else Polygon()
    
    obstaculos = []
    mesas_libres_cuadradas = []
    mesas_libres_redondas = []
    
    if 'objects' not in layout_actual or not isinstance(layout_actual['objects'], dict):
        # Manejo de error si la estructura del layout es incorrecta
        return {"perimetro_geom": perimetro_geom, "obstaculos_geom": MultiPolygon(), "mesas_libres_redondas": [], "mesas_libres_cuadradas": []}

    for obj_id, obj_data in layout_actual['objects'].items():
        
        # Filtro CRÍTICO: Procesar solo objetos que son MESAS.
        if not isinstance(obj_data, dict) or not obj_data.get('tipo', '').startswith('mesa'):
            continue

        # Lógica de Obstáculos: Si la mesa no se va a mover, es un obstáculo.
        if obj_id not in exclude_ids:
            # _get_object_footprint debe crear el "aura" roja que tú dibujaste.
            footprint = _get_object_footprint(obj_data, buffer=0.15)
            if footprint and not footprint.is_empty:
                obstaculos.append(footprint)

        # Lógica de Mesas Libres (sin cambios)
        if obj_data.get(STATE_KEY) == FREE_STATE:
            coords_m = obj_data.get('coords_mesa_metros')
            if not coords_m: continue

            mesa_geom = box(*coords_m)
            mesa_info = {
                'id': obj_id,
                'geom': mesa_geom,
                'capacidad_actual': obj_data.get(CAPACITY_KEY, 0),
                'sillas': obj_data.get(SILLAS_KEY, [])
            }
            if obj_data.get(TYPE_KEY) == TIPO_MESA_CUADRADA:
                mesas_libres_cuadradas.append(mesa_info)
            else:
                mesas_libres_redondas.append(mesa_info)

    obstaculos_geom = unary_union(obstaculos) if obstaculos else MultiPolygon()

    return {
        "perimetro_geom": perimetro_geom,
        "obstaculos_geom": obstaculos_geom,
        "mesas_libres_redondas": mesas_libres_redondas,
        "mesas_libres_cuadradas": mesas_libres_cuadradas,
    }

def _medir_mesas_promedio(layout_actual, mesas_ids):
    """Mide el tamaño promedio (largo y ancho) de una lista de mesas."""
    total_largo_m, total_ancho_m = 0, 0
    mesas_medidas = 0
    for mesa_id in mesas_ids:
        mesa_obj = layout_actual['objects'].get(mesa_id, {})
        coords = mesa_obj.get('coords_mesa_metros')
        if coords and len(coords) == 4:
            ancho = abs(coords[2] - coords[0])
            largo = abs(coords[3] - coords[1])
            total_largo_m += max(ancho, largo)
            total_ancho_m += min(ancho, largo)
            mesas_medidas += 1
    
    # Calcular promedio. Usar fallback si no se pudo medir.
    avg_largo_mesa_m = (total_largo_m / mesas_medidas) if mesas_medidas > 0 else 0.8
    avg_ancho_mesa_m = (total_ancho_m / mesas_medidas) if mesas_medidas > 0 else 0.8
    return avg_largo_mesa_m, avg_ancho_mesa_m

def _seleccionar_mesas_para_cluster(mesas_libres_cuadradas, num_people):
    """
    Selecciona el conjunto de mesas cuadradas más eficiente para formar un clúster.
    Estrategia: Ordena por capacidad y toma las más grandes primero (Ascendentemente).
    Devuelve (lista_ids_mesas, k) o (None, 0) si no es posible.
    """
    if not mesas_libres_cuadradas:
        return None, 0

    # Ordenar las mesas disponibles por capacidad, de mayor a menor.
    ordenadas = sorted(mesas_libres_cuadradas, key=lambda m: m.get('capacidad_actual', 0), reverse=True)
    
    seleccionadas_ids = []
    total_cap = 0
    for mesa in ordenadas:
        seleccionadas_ids.append(mesa['id'])
        total_cap += mesa.get('capacidad_actual', 0)
        if total_cap >= num_people:
            # En cuanto se cumple la capacidad, se devuelve el resultado.
            return seleccionadas_ids, len(seleccionadas_ids)

    # Si el bucle termina y no se alcanzó la capacidad, no es posible formar el clúster.
    return None, 0

def _build_cluster_template(k, ancho_mesa_m, largo_mesa_m, orientacion, sillas_data):
    """
    Construye la geometría EXACTA de un clúster (mesas + sillas) centrado en el origen,
    utilizando las dimensiones REALES de las sillas proporcionadas.
    Parámetros:
    - k: número de mesas en el clúster
    - ancho_mesa_m: ancho de cada mesa en metros
    - largo_mesa_m: largo de cada mesa en metros
    - orientacion: 'horizontal' o 'vertical'
    - sillas_data: lista de datos de sillas con sus dimensiones reales
    """

    geoms = []
    paso_distancia = largo_mesa_m + CLUSTER_PASS_BUFFER_M
    largo_total_cluster = (paso_distancia * k) - CLUSTER_PASS_BUFFER_M

    # Determinar posiciones de las mesas
    if orientacion == 'horizontal':
        start_x, start_y = -largo_total_cluster / 2 + largo_mesa_m / 2, 0
        step_x, step_y = paso_distancia, 0
    else: # vertical
        start_x, start_y = 0, -largo_total_cluster / 2 + largo_mesa_m / 2
        step_x, step_y = 0, paso_distancia

    mesas_virtuales_coords = []
    for i in range(k):
        cx = start_x + i * step_x
        cy = start_y + i * step_y
        mesas_virtuales_coords.append((cx, cy))
        half_w, half_l = ancho_mesa_m / 2.0, largo_mesa_m / 2.0
        geoms.append(box(cx - half_w, cy - half_l, cx + half_w, cy + half_l))

    # Distribuir sillas usando sus dimensiones reales
    num_people = len(sillas_data)
    sillas_por_mesa = num_people // k
    sillas_extra = num_people % k
    silla_idx_global = 0
    
    for i in range(k):
        cx, cy = mesas_virtuales_coords[i]
        num_sillas_esta_mesa = sillas_por_mesa + (1 if i < sillas_extra else 0)
        
        for j in range(num_sillas_esta_mesa):
            if silla_idx_global >= len(sillas_data): break
            silla_actual = sillas_data[silla_idx_global]
            silla_idx_global += 1
            # Usar las dimensiones REALES de la silla actual
            silla_coords_m = silla_actual.get('coords_metros', [0,0,0.5,0.5])
            silla_w_m = abs(silla_coords_m[2] - silla_coords_m[0])
            silla_h_m = abs(silla_coords_m[3] - silla_coords_m[1])
            half_silla_w, half_silla_h = silla_w_m / 2.0, silla_h_m / 2.0

            half_w, half_l = ancho_mesa_m / 2.0, largo_mesa_m / 2.0
            offset_x = half_w + half_silla_w + SILLA_BUFFER_M
            offset_y = half_l + half_silla_h + SILLA_BUFFER_M

            if orientacion == 'horizontal':
                sx, sy = (0, -offset_y) if j % 2 == 0 else (0, offset_y)
            else: # vertical
                sx, sy = (-offset_x, 0) if j % 2 == 0 else (offset_x, 0)
            
            s_cx, s_cy = cx + sx, cy + sy
            geoms.append(box(s_cx - half_silla_w, s_cy - half_silla_h, s_cx + half_silla_w, s_cy + half_silla_h))

    # Añadir un buffer a la plantilla del clúster para que tenga su propia "aura".
    # Este valor debe coincidir con el buffer de los obstáculos.
    cluster_geom = unary_union(geoms)
    return cluster_geom.buffer(0.15)

def _find_best_placement(cluster_horizontal, cluster_vertical, perimetro_geom, obstaculos_geom):
    """
    Encuentra la mejor posición probando AMBAS orientaciones (horizontal y vertical).
    Devuelve la primera posición válida que encuentra para simplificar.
    """
    espacio_libre_geom = perimetro_geom.difference(obstaculos_geom)
    if espacio_libre_geom.is_empty:
        return None

    minx, miny, maxx, maxy = espacio_libre_geom.bounds
    
    # Plantillas a probar: (plantilla, orientación)
    plantillas_a_probar = [
        (cluster_horizontal, 'horizontal'),
        (cluster_vertical, 'vertical')
    ]

    for plantilla, orientacion in plantillas_a_probar:
        if plantilla.is_empty:
            continue
            
        f_minx, f_miny, f_maxx, f_maxy = plantilla.bounds
        step = max(0.5, min(f_maxx - f_minx, f_maxy - f_miny) / 2.0)
        
        for x in np.arange(minx, maxx, step):
            for y in np.arange(miny, maxy, step):
                # Centramos la plantilla en el punto candidato
                cluster_movido = translate(plantilla, x, y)
                
                # Usamos una comprobación de área para mayor tolerancia
                interseccion = espacio_libre_geom.intersection(cluster_movido)
                if (interseccion.area / plantilla.area) >= 0.999:
                    print(f"Posición válida encontrada con orientación '{orientacion}' en ({x:.2f}, {y:.2f})")
                    posicion_encontrada = {'centro': Point(x, y), 'orientacion': orientacion}
                    
                    # Visualizar el plan correcto
                    # visualizar_geometrias(perimetro_geom, obstaculos_geom, espacio_libre_geom, cluster=plantilla, destino=posicion_encontrada)
                    return posicion_encontrada

    # No se encontró ninguna posición válida para el clúster.
    return None

def _apply_reservation(layout_actual, mesas_ids, num_people, user_id, reservation_time, movimiento_info):
    """
    Aplica la reserva moviendo el clúster a su destino.
    """
    reserva_id = f"R-{datetime.now().strftime('%Y%m%d%H%M%S')}-{user_id}"
    
    dims = layout_actual.get('dimensions', {})
    try:
        px_to_m_scale = float(dims.get('width_m')) / float(dims.get('width_px'))
        m_to_px = 1.0 / px_to_m_scale if px_to_m_scale != 0 else 20.0
    except Exception:
        m_to_px = 20.0

    if movimiento_info is None:
        # Reserva simple (sin cambios)
        for mesa_id in mesas_ids:
            layout_actual['objects'][mesa_id][STATE_KEY] = RESERVED_STATE
            layout_actual['objects'][mesa_id]['reserva_id'] = reserva_id
        return layout_actual, f"Reserva confirmada. Asignado a {', '.join(mesas_ids)}.", mesas_ids

    # --- Movimiento complejo ---
    destino = movimiento_info.get('destino', {})
    centro_destino_m = destino.get('centro')
    orientacion = destino.get('orientacion', 'vertical')
    angulo = 0 if orientacion == 'horizontal' else 90

    # 1. Recolectar datos para la ejecución
    k = movimiento_info.get('k')
    avg_ancho_mesa_m = movimiento_info.get('avg_ancho_mesa_m')
    avg_largo_mesa_m = movimiento_info.get('avg_largo_mesa_m')

    # Paso 1: Recolectar los datos de las sillas que se van a mover, SIN modificar el layout.
    sillas_a_mover_data = []
    sillas_a_mover_ids = set(movimiento_info.get('sillas_ids', []))
    for mesa_id in movimiento_info.get('mesas_ids', []):
        if mesa_id in layout_actual['objects']:
            mesa_obj = layout_actual['objects'][mesa_id]
            for silla in mesa_obj.get(SILLAS_KEY, []):
                if silla.get('id_silla') in sillas_a_mover_ids:
                    sillas_a_mover_data.append(silla)

    # Paso 2: Ahora que tenemos los datos, vaciar las sillas de sus mesas originales.
    for mesa_id in layout_actual['objects']:
        if not layout_actual['objects'][mesa_id].get('tipo', '').startswith('mesa'):
            continue
        
        mesa_obj = layout_actual['objects'][mesa_id]
        sillas_originales = mesa_obj.get(SILLAS_KEY, [])
        sillas_que_se_quedan = [s for s in sillas_originales if s.get('id_silla') not in sillas_a_mover_ids]
        
        mesa_obj[SILLAS_KEY] = sillas_que_se_quedan
        mesa_obj[CAPACITY_KEY] = len(sillas_que_se_quedan)

    # Ahora, mover las mesas seleccionadas y re-asignar las sillas
    mesas_a_mover_ids = movimiento_info.get('mesas_ids')
    
    paso_distancia = avg_largo_mesa_m + CLUSTER_PASS_BUFFER_M
    largo_total_cluster = (paso_distancia * k) - CLUSTER_PASS_BUFFER_M

    if orientacion == 'horizontal':
        start_x = -largo_total_cluster / 2 + avg_largo_mesa_m / 2
        start_y = 0
        step_x, step_y = paso_distancia, 0
    else: # vertical
        start_x = 0
        start_y = -largo_total_cluster / 2 + avg_largo_mesa_m / 2
        step_x, step_y = 0, paso_distancia
        
    start_point_final = translate(Point(start_x, start_y), xoff=centro_destino_m.x, yoff=centro_destino_m.y)

    sillas_por_mesa = num_people // k
    sillas_extra = num_people % k
    silla_idx_global = 0

    for i, mesa_id in enumerate(mesas_a_mover_ids):
        mesa_obj = layout_actual['objects'][mesa_id]
        
        # Calcular posición de la mesa
        cx = start_point_final.x + i * step_x
        cy = start_point_final.y + i * step_y
        
        half_w, half_l = avg_ancho_mesa_m / 2.0, avg_largo_mesa_m / 2.0
        coords_m = [cx - half_w, cy - half_l, cx + half_w, cy + half_l]
        
        mesa_obj.update({
            'coords_mesa_metros': coords_m,
            'coords_pixeles': [c * m_to_px for c in coords_m],
            'coords_mesa_pixeles': [c * m_to_px for c in coords_m],
            STATE_KEY: RESERVED_STATE, 'reserva_id': reserva_id,
            SILLAS_KEY: [], CAPACITY_KEY: 0
        })

        # Asignar y mover sillas
        num_sillas_esta_mesa = sillas_por_mesa + (1 if i < sillas_extra else 0)
        sillas_para_esta_mesa = []
        for j in range(num_sillas_esta_mesa):
            if silla_idx_global >= len(sillas_a_mover_data): break
            
            # En lugar de modificar una copia, obtenemos el diccionario de la silla
            silla_data = sillas_a_mover_data[silla_idx_global]
            
            silla_coords_m_orig = silla_data.get('coords_metros', [0,0,0.5,0.5])
            silla_w_m = abs(silla_coords_m_orig[2] - silla_coords_m_orig[0])
            silla_h_m = abs(silla_coords_m_orig[3] - silla_coords_m_orig[1])
            half_silla_w, half_silla_h = silla_w_m / 2.0, silla_h_m / 2.0

            offset_x = half_w + half_silla_w + SILLA_BUFFER_M
            offset_y = half_l + half_silla_h + SILLA_BUFFER_M

            if orientacion == 'horizontal':
                sx, sy = (0, -offset_y) if j % 2 == 0 else (0, offset_y)
            else: # vertical
                sx, sy = (-offset_x, 0) if j % 2 == 0 else (offset_x, 0)
            
            s_cx, s_cy = cx + sx, cy + sy
            silla_coords_m_final = [s_cx - half_silla_w, s_cy - half_silla_h, s_cx + half_silla_w, s_cy + half_silla_h]
            
            # Actualizamos el diccionario de la silla con sus nuevas coordenadas
            silla_data.update({
                'coords_metros': silla_coords_m_final,
                'coords_pixeles': [c * m_to_px for c in silla_coords_m_final]
            })
            
            # Añadimos el diccionario de la silla ya actualizado a la lista de la mesa
            sillas_para_esta_mesa.append(silla_data)
            silla_idx_global += 1
        
        mesa_obj[SILLAS_KEY] = sillas_para_esta_mesa
        mesa_obj[CAPACITY_KEY] = len(sillas_para_esta_mesa)

    return layout_actual, f"Reserva confirmada. Clúster asignado.", mesas_ids


def _apply_optimized_position(mesa_obj, movimiento_info, m_to_px):
    """
    Función LIGERA y PRECISA para aplicar una nueva posición a una mesa y sus sillas.
    Calcula las posiciones relativas para evitar errores de recálculo.
    """
    # 1. Extraer información del movimiento y la escala
    angulo = movimiento_info.get('angulo', 0)
    centro_destino_m = movimiento_info.get('centro')
    px_to_m = 1.0 / m_to_px if m_to_px != 0 else 0.05

    # 2. Calcular el centro original de la mesa
    mesa_coords_orig_m = mesa_obj.get('coords_mesa_metros', [0,0,1,1])
    centro_orig_x = (mesa_coords_orig_m[0] + mesa_coords_orig_m[2]) / 2.0
    centro_orig_y = (mesa_coords_orig_m[1] + mesa_coords_orig_m[3]) / 2.0

    # 3. Mover la mesa
    mesa_poly_orig = box(*mesa_coords_orig_m)
    # Mover la mesa original a su nueva posición
    mesa_poly_final = translate(rotate(mesa_poly_orig, angulo, origin=(centro_orig_x, centro_orig_y)),
                                xoff=centro_destino_m.x - centro_orig_x,
                                yoff=centro_destino_m.y - centro_orig_y)
    
    minx, miny, maxx, maxy = mesa_poly_final.bounds
    coords_m_rounded = [round(c, 3) for c in [minx, miny, maxx, maxy]]
    mesa_obj.update({
        'coords_mesa_metros': coords_m_rounded,
        'coords_pixeles': [c * m_to_px for c in coords_m_rounded],
        'coords_mesa_pixeles': [c * m_to_px for c in coords_m_rounded],
    })

    # 4. Mover las sillas aplicando la misma transformación
    for silla_data in mesa_obj.get(SILLAS_KEY, []):
        silla_coords_orig_m = silla_data.get('coords_metros', [0,0,0.5,0.5])
        silla_poly_orig = box(*silla_coords_orig_m)
        
        # Aplicar EXACTAMENTE la misma rotación y traslación que a la mesa
        silla_poly_final = translate(rotate(silla_poly_orig, angulo, origin=(centro_orig_x, centro_orig_y)),
                                     xoff=centro_destino_m.x - centro_orig_x,
                                     yoff=centro_destino_m.y - centro_orig_y)
        
        s_minx, s_miny, s_maxx, s_maxy = silla_poly_final.bounds
        silla_coords_m_rounded = [round(c, 3) for c in [s_minx, s_miny, s_maxx, s_maxy]]
        silla_data.update({
            'coords_metros': silla_coords_m_rounded,
            'coords_pixeles': [c * m_to_px for c in silla_coords_m_rounded]
        })
        
    return mesa_obj

def _find_most_distant_placement(cluster_footprint, perimetro_geom, obstaculos_existentes):
    """
    Encuentra la posición para el cluster que MAXIMIZA la distancia a los obstáculos existentes.
    Versión con lógica de distancia corregida.
    """
    espacio_libre_geom = perimetro_geom.difference(obstaculos_existentes)
    if espacio_libre_geom.is_empty:
        return None

    angles = [0, 90]
    minx, miny, maxx, maxy = espacio_libre_geom.bounds
    
    f_minx, f_miny, f_maxx, f_maxy = cluster_footprint.bounds
    step = max(1.0, min(f_maxx - f_minx, f_maxy - f_miny) / 2.0)    

    mejor_posicion = None
    max_distancia_minima = -1

    area_cluster = cluster_footprint.area
    TOLERANCIA_AREA = 0.999

    for angle in angles:
        cluster_rotado = rotate(cluster_footprint, angle, origin='center')
        
        for x in np.arange(minx, maxx, step):
            for y in np.arange(miny, maxy, step):
                punto_candidato = Point(x, y)
                
                if not espacio_libre_geom.contains(punto_candidato):
                    continue

                cluster_movido = translate(cluster_rotado, x, y)
                
                interseccion = espacio_libre_geom.intersection(cluster_movido)
                
                if (interseccion.area / area_cluster) >= TOLERANCIA_AREA:
                    distancia_actual = 0
                    if not obstaculos_existentes.is_empty:
                        # Si ya hay obstáculos, la distancia es a ellos.
                        distancia_actual = obstaculos_existentes.distance(cluster_movido)
                    else:
                        # Si es el primer objeto, la distancia es a los bordes del perímetro.
                        distancia_actual = perimetro_geom.exterior.distance(cluster_movido)
                    
                    if distancia_actual > max_distancia_minima:
                        max_distancia_minima = distancia_actual
                        mejor_posicion = {'centro': punto_candidato, 'angulo': angle}

    if mejor_posicion:
        print(f"  -> Mejor posición encontrada con una distancia de {max_distancia_minima:.2f}m.")
    
    return mejor_posicion


def optimizar_layout_completo(layout_actual):
    """
    Reorganiza TODAS las mesas del layout para DISTRIBUIRLAS equitativamente.
    Parametros:
    - layout_actual: El layout actual con todas las mesas.
    """
    
    # 1. PREPARAR DATOS
    geometria_base = _get_geometric_layout(layout_actual, exclude_ids=[])
    perimetro_geom = geometria_base['perimetro_geom']
    m_to_px = layout_actual.get('m_to_px', 20.0)
    
    # 2. CREAR PLANTILLAS CENTRADAS (AURAS)
    objetos_a_colocar = []
    for mesa_id, mesa_data in layout_actual['objects'].items():
        if not mesa_data.get('tipo', '').startswith('mesa'): continue
        footprint_original = _get_object_footprint(mesa_data, buffer=0.15)
        if footprint_original:
            cx, cy = footprint_original.centroid.x, footprint_original.centroid.y
            footprint_centrado = translate(footprint_original, xoff=-cx, yoff=-cy)
            objetos_a_colocar.append({
                'id': mesa_id, 'footprint': footprint_centrado, 'area': footprint_original.area
            })

    # 3. ORDENAR Y COLOCAR
    objetos_a_colocar.sort(key=lambda x: x['area'], reverse=True)
    obstaculos_colocados = MultiPolygon()
    posiciones_finales = {}
    
    for i, obj in enumerate(objetos_a_colocar):
        print(f"({i+1}/{len(objetos_a_colocar)}) Buscando posición para la mesa {obj['id']}...")
        
        # Usar la nueva función de búsqueda que maximiza la distancia
        posicion_encontrada = _find_most_distant_placement(obj['footprint'], perimetro_geom, obstaculos_colocados)

        if posicion_encontrada:
            posiciones_finales[obj['id']] = posicion_encontrada
            centro = posicion_encontrada['centro']
            angulo = posicion_encontrada['angulo']
            footprint_movido = translate(rotate(obj['footprint'], angulo, origin='center'), xoff=centro.x, yoff=centro.y)
            obstaculos_colocados = unary_union([obstaculos_colocados, footprint_movido])
        else:
            print(f"  -> ADVERTENCIA: No se encontró espacio para la mesa {obj['id']}.")

    # 4. RECONSTRUIR EL LAYOUT FINAL (Lógica sin cambios)
    layout_final = deepcopy(layout_actual)
    layout_final['objects'] = {}

    for mesa_id, movimiento_info in posiciones_finales.items():
        mesa_obj_original = deepcopy(layout_actual['objects'][mesa_id])
        mesa_obj_movido = _apply_optimized_position(mesa_obj_original, movimiento_info, m_to_px)
        layout_final['objects'][mesa_id] = mesa_obj_movido

    print("--- Optimización de Distribución Finalizada ---")
    return layout_final

def visualizar_geometrias(perimetro, obstaculos, espacio_libre, cluster=None, destino=None):
    """
    Crea una visualización de las geometrías usando Matplotlib.
    - perimetro: El polígono del perímetro del local.
    - obstaculos: El MultiPolygon de todos los obstáculos.
    - espacio_libre: La geometría resultante de la diferencia.
    - cluster: (Opcional) El footprint del clúster que se intenta colocar.
    - destino: (Opcional) El punto y ángulo donde se colocó el clúster.
    """
    fig, ax = plt.subplots(figsize=(10, 10))
    
    # Función auxiliar para dibujar cualquier geometría (Polygon o MultiPolygon)
    def plot_geom(geom, color, alpha, label):
        if geom.is_empty:
            return
        if isinstance(geom, Polygon):
            x, y = geom.exterior.xy
            ax.fill(x, y, alpha=alpha, fc=color, ec='black', label=label)
            for interior in geom.interiors:
                x, y = interior.xy
                ax.fill(x, y, alpha=1.0, fc='white', ec='black')
        elif isinstance(geom, MultiPolygon):
            first = True
            for poly in geom.geoms:
                x, y = poly.exterior.xy
                # Etiquetar solo el primer polígono de un multipolígono
                ax.fill(x, y, alpha=alpha, fc=color, ec='black', label=label if first else "")
                first = False
                for interior in poly.interiors:
                    x, y = interior.xy
                    ax.fill(x, y, alpha=1.0, fc='white', ec='black')

    # 1. Dibujar el perímetro completo en gris claro
    plot_geom(perimetro, 'lightgray', 1.0, 'Perímetro')

    # 2. Dibujar los obstáculos en rojo
    plot_geom(obstaculos, 'red', 0.5, 'Obstáculos (con Aura)')

    # 3. Dibujar el espacio libre calculado en verde
    plot_geom(espacio_libre, 'green', 0.6, 'Espacio Libre Calculado')
    
    # 4. Dibujar el clúster que se intenta colocar
    if cluster:
        plot_geom(cluster, 'blue', 0.5, 'Cluster a Colocar')
        
    # 5. Dibujar el clúster en su posición final
    if destino and cluster:
        from shapely.affinity import translate, rotate
        orientacion = destino.get('orientacion')
        if orientacion == 'horizontal':
            angulo = 0
        elif orientacion == 'vertical':
            angulo = 90
        else:
            angulo = destino.get('angulo', 0)
        
        cluster_final = translate(rotate(cluster, angulo, origin='center'), destino['centro'].x, destino['centro'].y)
        
        plot_geom(cluster_final, 'purple', 0.7, 'Posición Encontrada')

    ax.set_aspect('equal', adjustable='box')
    plt.title("Visualización del Espacio Libre")
    plt.legend()
    plt.grid(True)
    
    # En lugar de mostrar la ventana, la guardamos en un archivo.
    # Esto no bloquea el servidor Flask.
    plt.savefig('debug_plot.png')
    plt.close(fig) # Cierra la figura para liberar memoria.
    print("!!! Gráfico de depuración guardado en 'debug_plot.png' !!!")

def planificar_cluster_para_cliente(mesas_libres, party_size):
    """
    Función pública para ser usada por los endpoints.
    Encuentra el conjunto de mesas más eficiente para un cliente.
    Llama a la función interna _seleccionar_mesas_para_cluster.
    """
    # Filtra solo las mesas que se pueden agrupar (no redondas)
    mesas_clusterizables = [
        m for m in mesas_libres if 'redonda' not in m.get('tipo', '').lower()
    ]
    
    # Llama a la función interna que contiene la lógica "Greedy"
    ids_seleccionados, _ = _seleccionar_mesas_para_cluster(mesas_clusterizables, party_size)
    
    # Devuelve los IDs y un mensaje (o None si no se encontró)
    if ids_seleccionados:
        return ids_seleccionados, "Clúster eficiente encontrado."
    
    return None, "No se pudo formar un clúster con la capacidad requerida."

def generar_layout_simulado_para_hora(target_time, layout_id=None):
    """
    Genera una representación JSON del layout para una hora específica.
    Si se provee un layout_id, usa ese layout. Si no, usa el que esté activo.
    """
    RESERVATION_DURATION = timedelta(minutes=120)
    target_end_time = target_time + RESERVATION_DURATION
    earliest_start_time = target_time - RESERVATION_DURATION

    layout_db = None
    if layout_id:
        # --- CAMBIO: Si se pasa un ID, buscar ese layout específico ---
        layout_db = Layout.query.get(layout_id)
        if not layout_db:
            return None, f"No se encontró el layout con ID {layout_id}."
    else:
        # --- Lógica existente: Buscar el layout activo ---
        layout_db = Layout.query.filter_by(is_active=True).first()
    
    if not layout_db:
        return None, "No hay ningún layout activo configurado en el sistema."

    # Construir el layout base desde la BD (sin cambios)
    layout_base = {
        "dimensions": {"width_px": layout_db.width_px, "height_px": layout_db.height_px, "width_m": layout_db.width_m, "height_m": layout_db.height_m},
        "objects": {
            mesa.id_str: {
                "id": mesa.id_str, "tipo": mesa.tipo, "estado": 'libre', # El estado base siempre es 'libre'
                "capacidad_actual": mesa.capacidad_actual, "coords_mesa_metros": mesa.coords_mesa_metros,
                "coords_mesa_pixeles": mesa.coords_mesa_pixeles,
                "sillas_asignadas": [{"id_silla": s.id_str, "coords_pixeles": s.coords_pixeles, "coords_metros": s.coords_metros, "tipo": s.tipo} for s in mesa.sillas]
            } for mesa in layout_db.mesas
        },
        "perimeter": {"points": json.loads(layout_db.perimeter_json)}, "m_to_px": layout_db.m_to_px
    }
    
    # Obtener todas las reservas que se solapan con el tiempo deseado
    # Y que PERTENECEN EXCLUSIVAMENTE al layout que se está simulando.
    reservas_activas = db.session.query(Reserva).filter(
        Reserva.layout_id == layout_db.id, # <-- ESTA ES LA LÍNEA CLAVE
        Reserva.status == 'activa',
        Reserva.reservation_time < target_end_time,
        Reserva.reservation_time > earliest_start_time
    ).all()

    layout_simulado = deepcopy(layout_base)
    
    # Aplicar el estado de cada reserva al layout simulado
    for reserva in reservas_activas:
        if reserva.movimiento_info_json:
            # Simular movimiento de clúster
            movimiento_info_copia = deepcopy(reserva.movimiento_info_json)
            centro_destino = Point(movimiento_info_copia['destino']['centro_x'], movimiento_info_copia['destino']['centro_y'])
            movimiento_info_copia['destino']['centro'] = centro_destino
            
            # 1. Guardar las sillas de las mesas que se van a mover ANTES de la simulación.
            sillas_a_restaurar = {}
            for mesa_id in movimiento_info_copia['mesas_ids']:  
                if mesa_id in layout_simulado['objects']:
                    sillas_a_restaurar[mesa_id] = layout_simulado['objects'][mesa_id].get('sillas_asignadas', [])

            # 2. Llamar a la función que mueve las mesas (y que actualmente pierde las sillas).
            layout_simulado, _, _ = _apply_reservation(
                layout_simulado,
                movimiento_info_copia['mesas_ids'],
                reserva.num_people,
                reserva.user_id,
                reserva.reservation_time.isoformat(),
                movimiento_info_copia
            )

            # 3. Restaurar la información de las sillas en las mesas ya movidas.
            for mesa_id, sillas in sillas_a_restaurar.items():
                if mesa_id in layout_simulado['objects']:
                    layout_simulado['objects'][mesa_id]['sillas_asignadas'] = sillas
        else:
            for mesa in reserva.mesas:
                if mesa.id_str in layout_simulado['objects']:
                    layout_simulado['objects'][mesa.id_str]['estado'] = 'reservado'
    
    return layout_simulado, None