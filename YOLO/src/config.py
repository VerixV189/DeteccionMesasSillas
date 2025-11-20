import os

# Obtener la ruta base del proyecto
basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    """Configuración base, compartida por todos los entornos."""
    # Lee la SECRET_KEY desde el entorno, con un valor por defecto por si no existe.
    SECRET_KEY = os.getenv('SECRET_KEY', 'un-valor-por-defecto-solo-para-emergencias')
    
    MODEL_PATH = "weights/best.pt"
    UPLOAD_FOLDER = "uploads"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Lee la URL de la base de datos desde el entorno.
    # Si no existe, construye una para SQLite por defecto.
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, '..', 'app.db')

class DevelopmentConfig(Config):
    """Configuración para el entorno de desarrollo."""
    DEBUG = True

class ProductionConfig(Config):
    """Configuración para el entorno de producción."""
    DEBUG = False
    # En producción, podrías construir la URL de la BD con las otras variables
    # SQLALCHEMY_DATABASE_URI = f"postgresql://{os.getenv('DATABASE_USER')}:{os.getenv('DATABASE_PASSWORD')}@{os.getenv('DATABASE_HOST')}/{os.getenv('DATABASE_NAME')}"

# Un diccionario para acceder a las clases de configuración por nombre
config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}