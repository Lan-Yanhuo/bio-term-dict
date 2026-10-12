from flask import Blueprint, render_template
upload_bp = Blueprint('upload', __name__)

@upload_bp.route('/upload')
def upload_page():
    return "这是上传页面 (upload page)" 