import os
from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO

db = SQLAlchemy()
socketio = SocketIO()

def create_app():
    app = Flask(__name__)
# 数据库配置：SQLite 存放在 instance 目录下
    base_dir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(base_dir, 'instance', 'terms.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = 'bio-term-dict-secret-key-2024'
    app.config['UPLOAD_FOLDER'] = os.path.join(base_dir, 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(base_dir, 'instance'), exist_ok=True)
    db.init_app(app)
    socketio.init_app(app, async_mode='threading', cors_allowed_origins='*')
    from routes.upload import upload_bp
    from routes.search import search_bp
    from routes.category import category_bp
    from routes.graph import graph_bp

    app.register_blueprint(upload_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(category_bp)
    app.register_blueprint(graph_bp)
    @app.route('/')
    def index():
        return redirect(url_for('upload.upload_page'))
    return app
if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        from models.database import Paper, Term
        db.create_all()
    socketio.run(app, debug=True, host='127.0.0.1', port=5000)