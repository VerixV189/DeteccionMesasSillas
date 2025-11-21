import math
import cv2
import numpy as np
import uuid

def procesar_detecciones(results, local_ancho_m, local_alto_m, filtros_m,):
    """
    Versión restaurada y simplificada. Procesa las detecciones del modelo YOLO,
    convierte coordenadas a metros y filtra los objetos según su tamaño.
    Parámetros:
    - results: Resultados de la detección del modelo YOLO.
    - local_ancho_m: Ancho del plano en metros.
    - local_alto_m: Alto del plano en metros.
    - filtros_m: Diccionario con filtros de tamaño para mesas y sillas.
    """
    lista_mesas = []
    lista_sillas = []
    classNames = getattr(results, "names", {}) or {}

    # 1. Calcular el factor de escala (píxeles por metro)
    try:
        ancho_plano_pixeles = int(results.orig_shape[1])
        factor_escala = float(ancho_plano_pixeles) / float(local_ancho_m)
    except (AttributeError, TypeError, ValueError):
        return [], []

    # 2. Identificar qué IDs de clase corresponden a mesas y sillas
    ID_MESAS = [i for i, name in classNames.items() if 'mesa' in str(name).lower()]
    ID_SILLAS = [i for i, name in classNames.items() if 'silla' in str(name).lower()]

    # 3. Procesar cada objeto detectado
    for box in results.boxes:
        confianza = float(box.conf[0])
        if confianza < 0.25:
            continue

        class_id = int(box.cls[0])
        es_mesa = class_id in ID_MESAS
        es_silla = class_id in ID_SILLAS

        if not es_mesa and not es_silla:
            continue

        # 4. Conversión de coordenadas y cálculo de dimensiones
        coords_px = [float(x) for x in box.xyxy[0].tolist()]
        x1_p, y1_p, x2_p, y2_p = coords_px
        
        coords_m = [coord / factor_escala for coord in coords_px]
        ancho_metros = (x2_p - x1_p) / factor_escala
        alto_metros = (y2_p - y1_p) / factor_escala
        lado_max_metros = max(ancho_metros, alto_metros)

        # 5. Aplicar filtros de tamaño
        filtro_tipo = "MESA" if es_mesa else "SILLA"
        filtro = filtros_m.get(filtro_tipo, {"min_lado": 0, "max_lado": 999})

        if filtro["min_lado"] <= lado_max_metros <= filtro["max_lado"]:
            item = {
                'coords_pixeles': coords_px,
                'coords_metros': coords_m,
                'ancho_m': ancho_metros,
                'alto_m': alto_metros,
                'clase_id': class_id,
                'nombre_clase': classNames.get(class_id, "desconocido"),
            }

            if es_mesa:
                # Generar un ID único y corto para la mesa
                item['id_mesa'] = f"M-{uuid.uuid4().hex[:6]}"
                lista_mesas.append(item)
                # mesa_counter += 1 # Eliminado
            elif es_silla:
                # Generar un ID único y corto para la silla
                item['id_silla'] = f"S-{uuid.uuid4().hex[:6]}"
                lista_sillas.append(item)
                # silla_counter += 1 # Eliminado

    return lista_mesas, lista_sillas

def calcular_centro(coords):
    """Calcula el punto central de un cuadro delimitador."""
    x1, y1, x2, y2 = coords
    return ((x1 + x2) / 2, (y1 + y2) / 2)

def agrupar_mesas_sillas(lista_mesas, lista_sillas):
    """
    Versión restaurada. Asigna cada silla a la mesa más cercana.
    Esta versión guarda solo la información esencial para la visualización inicial.
    Parametros:
    - lista_mesas: Lista de diccionarios con información de mesas detectadas.
    - lista_sillas: Lista de diccionarios con información de sillas detectadas.
    """
    layout_ordenado = {}

    # 1. Añadir todas las mesas al layout
    for mesa in lista_mesas:
        id_mesa_actual = mesa['id_mesa']
        layout_ordenado[id_mesa_actual] = {
            "coords_mesa_pixeles": mesa['coords_pixeles'],
            "coords_mesa_metros": mesa['coords_metros'],
            "sillas_asignadas": [],
            "capacidad_actual": 0,
            "tipo": mesa['nombre_clase'].lower(),
            "estado": "libre"
        }

    # 2. Asignar cada silla a la mesa más cercana
    for silla in lista_sillas:
        centro_silla_metros = calcular_centro(silla['coords_metros'])
        distancia_minima = float('inf')
        id_mesa_cercana = None

        for id_mesa, datos_mesa in layout_ordenado.items():
            centro_mesa_metros = calcular_centro(datos_mesa['coords_mesa_metros'])
            dist = math.sqrt((centro_silla_metros[0] - centro_mesa_metros[0])**2 + (centro_silla_metros[1] - centro_mesa_metros[1])**2)
            if dist < distancia_minima:
                distancia_minima = dist
                id_mesa_cercana = id_mesa

        # 3. Añadir la silla a la mesa encontrada
        if id_mesa_cercana is not None:
            layout_ordenado[id_mesa_cercana]['sillas_asignadas'].append({
                'id_silla': silla['id_silla'],
                'coords_pixeles': silla['coords_pixeles'],
                'coords_metros': silla['coords_metros'],
                'tipo': silla['nombre_clase'].lower()
            })
            layout_ordenado[id_mesa_cercana]['capacidad_actual'] += 1

    # 4. El paso de limpieza ya no es necesario.
    return layout_ordenado