from . import db
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from datetime import datetime

reserva_mesas = db.Table('reserva_mesas',
    db.Column('reserva_id', db.Integer, db.ForeignKey('reserva.id'), primary_key=True),
    db.Column('mesa_id', db.Integer, db.ForeignKey('mesa.id'), primary_key=True)
)

class Layout(db.Model):
    __tablename__ = 'layout'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    width_px = db.Column(db.Float, nullable=False)
    height_px = db.Column(db.Float, nullable=False)
    width_m = db.Column(db.Float, nullable=False)
    height_m = db.Column(db.Float, nullable=False)
    m_to_px = db.Column(db.Float, nullable=False)
    perimeter_json = db.Column(db.Text) # Guardar el perímetro como un string JSON
    mesas = db.relationship('Mesa', backref='layout', lazy=True, cascade="all, delete-orphan")

class Mesa(db.Model):
    __tablename__ = 'mesa'
    id = db.Column(db.Integer, primary_key=True)
    layout_id = db.Column(db.Integer, db.ForeignKey('layout.id'), nullable=False)
    
    id_str = db.Column(db.String(50), nullable=False)
    tipo = db.Column(db.String(50))
    estado = db.Column(db.String(50), default='libre')
    capacidad_actual = db.Column(db.Integer)
    coords_mesa_pixeles = db.Column(ARRAY(db.Float))
    coords_mesa_metros = db.Column(ARRAY(db.Float))
    angle = db.Column(db.Float, default=0.0)
    
    sillas = db.relationship('Silla', backref='mesa', lazy=True, cascade="all, delete-orphan")

class Silla(db.Model):
    __tablename__ = 'silla'
    id = db.Column(db.Integer, primary_key=True)
    mesa_id = db.Column(db.Integer, db.ForeignKey('mesa.id'), nullable=False)
    id_str = db.Column(db.String(50), nullable=False)
    tipo = db.Column(db.String(50))
    coords_pixeles = db.Column(ARRAY(db.Float))
    coords_metros = db.Column(ARRAY(db.Float))
    
    angle = db.Column(db.Float, default=0.0)

class Reserva(db.Model):
    __tablename__ = 'reserva'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(100))
    num_people = db.Column(db.Integer)
    reservation_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='activa', nullable=False) # 'activa', 'cancelada', 'completada'
    movimiento_info_json = db.Column(JSONB, nullable=True) 
    mesas = db.relationship('Mesa', secondary=reserva_mesas, lazy='subquery',
        backref=db.backref('reservas', lazy=True))