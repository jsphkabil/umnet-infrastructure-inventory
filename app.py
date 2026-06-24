from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
# This creates a local database file called 'inventory.db'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
db = SQLAlchemy(app)

### Data models
class Product(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100), nullable=False)

class StorageContainer(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    type = db.Column(db.String(20)) # Shelf or Pallet
    status = db.Column(db.String(20), default='In Room') # 'In Room' or 'Shipped'

class InventoryLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.String(50), db.ForeignKey('product.id'))
    container_id = db.Column(db.String(50), db.ForeignKey('storage_container.id'))
    quantity = db.Column(db.Integer, nullable=False)

### Controller (routes)
@app.route('/')
def dashboard():
    # Fetch all items where status is 'In Room'
    active_inventory = db.session.query(
        Product.name,
        StorageContainer.id.label('container'),
        StorageContainer.type,
        InventoryLog.quantity
    ).join(InventoryLog, Product.id == InventoryLog.product_id)\
    .join(StorageContainer, InventoryLog.container_id == StorageContainer.id)\
    .filter(StorageContainer.status == 'In Room').all()

    # Render frontend HTML file and pass database data into it
    return render_template('dashboard.html', inventory=active_inventory)

@app.route('/add', methods=['POST'])
def add_inventory():
    #Grab data out of the HTML form fields
    p_id = request.form['product_id']
    p_name = request.form['product_name']
    c_id = request.form['container_id']
    c_type = request.form['container_type']
    qty = int(request.form['quantity'])

    # Check if product exists
    product = Product.query.get(p_id)
    if not product:
        product = Product(id=p_id, name=p_name)
        db.session.add(product)

    # Check if container exists
    container = StorageContainer(id=c_id, type=c_type)
    if not container:
        container = StorageContainer(id=c_id, type=c_type, status='In Room')
        db.session.add(container)

    # Create inventory linkage log entry
    log_entry = InventoryLog(product_id=p_id, container_id=c_id, quantity=qty)
    db.session.add(log_entry)

    db.session.commit()

    return redirect('/')

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)