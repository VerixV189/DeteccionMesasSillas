from ultralytics import YOLO
# from IPython.display import display, Image
from recursos import agrupar_mesas_sillas, procesar_detecciones;



model = YOLO("weights/best.pt")
image = "10.png"
print('-----------------')
print("Processing image:", image)
print('-----------------')
resultsList = model.predict(source=image, conf=0.25, iou=0.45)#, save=True)
results = resultsList[0]

# lista_mesas, lista_sillas = procesar_detecciones(results)
# layout_ordenado = agrupar_mesas_sillas(lista_mesas, lista_sillas)
# print('-----------------')
# print(layout_ordenado)
# print('-----------------')
classNames = results.names

# listaDeMesasCuadradas = []
# listaDeMesasRedondas = []
# listaDeSillasCuadradas = []
# listaDeSillasRedondas = []

for box in results.boxes:
    
    # 1. ACCEDER AL ID DE LA CLASE
    # box.cls es un tensor (ej. [3.]), usamos [0] e int() para obtener el número 3
    class_id = int(box.cls[0])
    
    # 2. ACCEDER A LA CONFIANZA
    # box.conf es un tensor (ej. [0.95]), usamos [0] y float() para obtener 0.95
    confianza = float(box.conf[0])
    
    # 3. ACCEDER A LAS COORDENADAS
    # box.xyxy es un tensor (ej. [[100, 150, 200, 250]]), usamos [0]
    coords = box.xyxy[0]
    
    # Opcional: Separar las coordenadas
    x1 = coords[0]
    y1 = coords[1]
    x2 = coords[2]
    y2 = coords[3]
    
    # (Opcional) Obtener el nombre de la clase
    nombre_clase = classNames[class_id]
    
    # -----------------------------------------------------------------
    # AHORA PUEDES USAR LOS DATOS
    # -----------------------------------------------------------------
    
    # Aplicamos el filtro NMS que discutimos
    if confianza > 0.5:
        print("--- Detección Encontrada ---")
        print(f"  Clase: {nombre_clase} (ID: {class_id})")
        print(f"  Confianza: {confianza:.2f}") # .2f redondea a 2 decimales
        print(f"  Coordenadas (x1, y1): ({x1:.0f}, {y1:.0f})") # .0f quita decimales
        print(f"  Coordenadas (x2, y2): ({x2:.0f}, {y2:.0f})")

print('--------------------------------------------------------------------')
for box in results.boxes:
    classId = int(box.cls)
    className = classNames[classId]

    coords = box.xyxy[0]
    confianza = float(box.conf[0])

    if confianza >= 0.25:
        # x1, y1, x2, y2 = map(float, coords)
        # boxInfo = {
        #     "class": className,
        #     "confidence": confianza,
        #     "box": [x1, y1, x2, y2]
        # }

        if className == "mesas_cuadradas":
            listaDeMesasCuadradas.append({'nombre': className, 'coords': coords, 'conf': confianza})
        elif className == "mesas_redondas":
            listaDeMesasRedondas.append({'nombre': className, 'coords': coords, 'conf': confianza})
        elif className == "sillas_cuadradas":
            listaDeSillasCuadradas.append({'nombre': className, 'coords': coords, 'conf': confianza})
        elif className == "sillas_redondas":
            listaDeSillasRedondas.append({'nombre': className, 'coords': coords, 'conf': confianza})

print(f"Mesas detectadas (filtradas): {len(listaDeMesasCuadradas)}")
print(f"Mesas detectadas (filtradas): {len(listaDeMesasRedondas)}")
print(f"Sillas detectadas (filtradas): {len(listaDeSillasCuadradas)}")
print(f"Sillas detectadas (filtradas): {len(listaDeSillasRedondas)}")
print(classNames)
# print(results)
print('-----------------')










