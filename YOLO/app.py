import os
from dotenv import load_dotenv # <-- 1. Importar load_dotenv
from src import create_app

# --- INICIO DE LA CORRECCIÓN ---
# 2. Cargar las variables del archivo .env en el entorno del sistema
load_dotenv()
# --- FIN DE LA CORRECCIÓN ---

# Ahora os.getenv('FLASK_ENV') funcionará correctamente
config_name = os.getenv('FLASK_ENV', 'development')

app = create_app(config_name)

if __name__ == '__main__':
    app.run()