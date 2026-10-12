from flask import Blueprint
graph_bp = Blueprint('graph', __name__)

@graph_bp.route('/graph')
def graph_page():
    return "这是图谱页面"