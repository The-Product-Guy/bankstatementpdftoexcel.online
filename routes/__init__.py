from routes.auth import auth_bp
from routes.billing import billing_bp
from routes.converter import converter_bp
from routes.pages import pages_bp

all_blueprints = [auth_bp, billing_bp, converter_bp, pages_bp]
