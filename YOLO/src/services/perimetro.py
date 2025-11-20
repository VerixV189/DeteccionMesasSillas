import cv2

def detectar_perimetro(path_imagen):
    """
    Detecta el contorno externo (perímetro del restaurante) en una imagen.
    Devuelve una lista de puntos [(x1, y1), (x2, y2), ...].
    Parámetros:
    - path_imagen: Ruta a la imagen del plano del restaurante.
    """
    img = cv2.imread(path_imagen)
    if img is None:
        raise FileNotFoundError(f"Imagen no encontrada: {path_imagen}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

    # Buscar contornos externos
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        # Fallback: usar el rectángulo completo de la imagen como perímetro
        h, w = img.shape[:2]
        return [(0, 0), (w, 0), (w, h), (0, h)]

    # Tomar el contorno más grande (probablemente el recinto)
    contour = max(contours, key=cv2.contourArea)

    # Simplificar el contorno
    epsilon = 0.01 * cv2.arcLength(contour, True)
    polygon = cv2.approxPolyDP(contour, epsilon, True)

    # Convertir a lista de tuplas
    puntos = [(int(p[0][0]), int(p[0][1])) for p in polygon]

    return puntos
