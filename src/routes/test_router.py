

from flask import Blueprint, jsonify


test_bp = Blueprint('test_bp', __name__)

@test_bp.route('/saludo/', methods=['GET'])
def mostrar_saludo():
    return jsonify({
        "saludo":"Saludos desde Servidor Flask"
    })