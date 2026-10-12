from flask import Blueprint
category_bp = Blueprint('category', __name__)

@category_bp.route('/category')
def category_page():
    return "这是分类页面"