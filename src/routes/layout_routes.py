from flask import Blueprint, request, jsonify, current_app
from PIL import Image
import os
import json

from ..services.recursos import procesar_detecciones, agrupar_mesas_sillas
from ..services.perimetro import detectar_perimetro
from ..services.optimizador import optimizar_layout_completo

from .. import db
from ..models import Layout, Mesa, Silla

layout_bp = Blueprint('layout_bp', __name__)

@layout_bp.route('/detect', methods=['POST'])
def detect_objects():
    if 'plano_imagen' not in request.files:
        return jsonify({"error": "No se envió ninguna imagen"}), 400

    file = request.files['plano_imagen']
    model = current_app.model
    if model is None:
        return jsonify({"error": "Modelo YOLO no disponible en el servidor."}), 503

    filename = file.filename
    path_guardado = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(path_guardado)

    img = Image.open(path_guardado)
    ancho_plano_pixeles = img.width
    alto_plano_pixeles = img.height

    try:
        config_data = json.loads(request.form['config'])
        local_ancho_m = config_data['local_ancho_m']
        local_alto_m = config_data['local_alto_m']
        filtros_m = config_data['filtros_m']
    except Exception as e:
        return jsonify({"error": f"Error al parsear la configuración: {e}"}), 400

    # Ejecutar predicción
    results_list = model.predict(source=path_guardado, conf=0.25, iou=0.45, save=False)
    results = results_list[0]

    poligono_perimetro = detectar_perimetro(path_guardado)

    lista_mesas, lista_sillas = procesar_detecciones(
        results,
        local_ancho_m, 
        local_alto_m, 
        filtros_m
    )

    layout_ordenado = agrupar_mesas_sillas(lista_mesas, lista_sillas)

    # Calcular el factor de conversión de metros a píxeles.
    try:
        m_to_px_scale = float(ancho_plano_pixeles) / float(local_ancho_m)
    except (TypeError, ZeroDivisionError):
        m_to_px_scale = 20.0 

    layout_data = {
        "dimensions": {
            "width_px": ancho_plano_pixeles,
            "height_px": alto_plano_pixeles,
            "width_m": local_ancho_m,
            "height_m": local_alto_m,
        },
        "objects": layout_ordenado,
        "perimeter": {
            "points": poligono_perimetro
        },
        "m_to_px": m_to_px_scale 
    }

    return jsonify(layout_data), 200

@layout_bp.route('/optimize', methods=['POST'])
def optimize_current_layout():
    """
    Toma un layout enviado en el cuerpo de la petición, lo optimiza y lo devuelve.
    """
    body = request.get_json()
    if not body:
        return jsonify({"error": "No se enviaron datos de layout en el cuerpo de la petición."}), 400
    
    layout_a_optimizar = body.get('layout', body)
    
    layout_optimizado = optimizar_layout_completo(layout_a_optimizar)
    
    return jsonify(layout_optimizado), 200

@layout_bp.route('', methods=['POST'])
def save_layout():
    body = request.get_json()
    if not body:
        return jsonify({"error": "No hay layout en el cuerpo de la petición para guardar."}), 400
        
    data = body.get('layout', body)
    
    Layout.query.delete()
    db.session.commit()
    
    new_layout = Layout(
        name="Plano Principal",
        width_px=data['dimensions']['width_px'],
        height_px=data['dimensions']['height_px'],
        width_m=data['dimensions']['width_m'],
        height_m=data['dimensions']['height_m'],
        m_to_px=data.get('m_to_px', 20.0),
        perimeter_json=json.dumps(data['perimeter']['points'])
    )
    db.session.add(new_layout)
    
    for obj_id, obj_data in data.get('objects', {}).items():
        if obj_data.get('tipo', '').startswith('mesa'):
            new_mesa = Mesa(
                layout=new_layout,
                id_str=obj_id,
                tipo=obj_data.get('tipo'),
                estado=obj_data.get('estado', 'libre'),
                capacidad_actual=obj_data.get('capacidad_actual', 0),
                
                # Mapeo directo del JSON a las columnas de la BD
                coords_mesa_pixeles=obj_data.get('coords_mesa_pixeles'),
                coords_mesa_metros=obj_data.get('coords_mesa_metros'),
                
                angle=obj_data.get('angle', 0.0)
            )
            db.session.add(new_mesa)
            
            for silla_data in obj_data.get('sillas_asignadas', []):
                new_silla = Silla(
                    mesa=new_mesa,
                    id_str=silla_data.get('id_silla'),
                    tipo=silla_data.get('tipo'),
                    
                    # Mapeo directo del JSON a las columnas de la BD
                    coords_pixeles=silla_data.get('coords_pixeles'),
                    coords_metros=silla_data.get('coords_metros'),
                    
                    angle=silla_data.get('angle', 0.0)
                )
                db.session.add(new_silla)

    db.session.commit()
    return jsonify({"message": "Layout completo guardado en la base de datos con éxito."}), 201

@layout_bp.route('/<int:layout_id>', methods=['GET'])
def load_layout(layout_id):
    return jsonify({"message": "Función para cargar no implementada aún."})