from flask import Blueprint
search_bp = Blueprint('search', __name__)

@search_bp.route('/search')
def search_page():
    return "这是搜索页面"