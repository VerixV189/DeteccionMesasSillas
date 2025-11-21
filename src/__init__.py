from flask import Flask
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
from ultralytics import YOLO
from .config import config_by_name

db = SQLAlchemy()
migrate = Migrate()

def create_app(config_name='default'):
    """
    Fábrica de la aplicación.
    """
    app = Flask(__name__)
    config_object = config_by_name[config_name]
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)

    CORS(app, resources={r"/api/*": {"origins": ["*"]}})
    
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    try:
        model_path = app.config['MODEL_PATH']
        app.model = YOLO(model_path)
        print(f"INFO: Modelo YOLO cargado correctamente desde '{model_path}'.")
    except Exception as e:
        print(f"ERROR: No se pudo cargar el modelo YOLO: {e}")
        app.model = None

    app.layout_cache = {}

    from .routes.layout_routes import layout_bp
    from .routes.reserva_routes import reserva_bp
    from .routes.test_router import test_bp
    from . import models 

    app.register_blueprint(layout_bp, url_prefix='/api/layout')
    app.register_blueprint(reserva_bp, url_prefix='/api/reserva')
    app.register_blueprint(test_bp,url_prefix='/api/test')
    
    return app