from flask import Flask, render_template, request, redirect, url_for, jsonify
from models import db, EquipmentModel, PhysicalAllocation

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///netinfra_warehouse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Context processor helper to populate initial demo data if database is empty
def seed_database_if_empty():
    if EquipmentModel.query.count() == 0:
        cisco = EquipmentModel(model_name="Cisco Catalyst 9300 48P", sku="CISCO-C9300-48P")
        juniper = EquipmentModel(model_name="Juniper EX3300 24T", sku="JNPR-EX3300-24T")
        
        db.session.add_all([cisco, juniper])
        db.session.commit()
        
        loc1 = PhysicalAllocation(model_id=cisco.id, container_id="PALLET-04", container_type="Pallet Array", quantity=40)
        loc2 = PhysicalAllocation(model_id=cisco.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=12)
        loc3 = PhysicalAllocation(model_id=juniper.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=12)
        
        db.session.add_all([loc1, loc2, loc3])
        db.session.commit()

@app.route('/')
def dashboard():
    # Fetch all equipment models to populate the global catalog list view
    catalog = EquipmentModel.query.all()
    
    # Target the first item by default if data exists
    selected_model = catalog[0] if catalog else None
    return render_template('dashboard.html', catalog=catalog, selected_model=selected_model)


@app.route('/api/model/<int:model_id>/locations', methods=['GET'])
def get_model_locations(model_id):
    """API endpoint triggered when a user clicks a catalog item on the left."""
    model = EquipmentModel.query.get_or_404(model_id)
    allocations = [{
        'id': alloc.id,
        'container_id': alloc.container_id,
        'container_type': alloc.container_type,
        'quantity': alloc.quantity
    } for alloc in model.allocations]
    
    return jsonify({
        'model_name': model.model_name,
        'global_total': model.global_total,
        'allocations': allocations
    })


@app.route('/api/allocation/<int:alloc_id>/update', methods=['POST'])
def update_quantity(alloc_id):
    """API endpoint triggered by the direct inline +/- counter buttons."""
    data = request.get_json()
    new_qty = data.get('quantity')
    
    allocation = PhysicalAllocation.query.get_or_404(alloc_id)
    if new_qty is not None and new_qty >= 0:
        allocation.quantity = int(new_qty)
        db.session.commit()
        return jsonify({'success': True, 'new_global_total': allocation.hardware_profile.global_total})
    
    return jsonify({'success': False, 'error': 'Invalid quantity balance value'}), 400


@app.route('/catalog/add', methods=['POST'])
def add_new_sku():
    """Form processing to register a new physical hardware asset master type."""
    name = request.form.get('model_name')
    sku = request.form.get('sku')
    
    if name and sku:
        new_model = EquipmentModel(model_name=name, sku=sku)
        db.session.add(new_model)
        db.session.commit()
        
    return redirect(url_for('dashboard'))


@app.route('/location/assign', methods=['POST'])
def assign_routing_destination():
    """Form processing to drop an existing model into a completely new location box."""
    # We pass the currently active focused model ID from the UI hidden inputs or query strings
    model_id = request.form.get('active_model_id')
    container_id = request.form.get('container_id').strip().upper()
    container_type = request.form.get('container_type')
    initial_qty = int(request.form.get('initial_qty', 0))
    
    if model_id and container_id:
        # Check if this exact model is already mapped to that container
        existing = PhysicalAllocation.query.filter_by(model_id=model_id, container_id=container_id).first()
        if existing:
            existing.quantity += initial_qty
        else:
            new_alloc = PhysicalAllocation(
                model_id=model_id,
                container_id=container_id,
                container_type=container_type,
                quantity=initial_qty
            )
            db.session.add(new_alloc)
        db.session.commit()
        
    return redirect(url_for('dashboard'))

@app.route('/api/container/<container_id>/items', methods=['GET'])
def get_container_items(container_id):
    """API endpoint triggered when a user clicks a physical location item in the sidebar."""
    # Find all allocations assigned to this specific container
    allocations = PhysicalAllocation.query.filter_by(container_id=container_id).all()
    
    item_list = [{
        'alloc_id': alloc.id,
        'model_name': alloc.hardware_profile.model_name,
        'sku': alloc.hardware_profile.sku,
        'quantity': alloc.quantity
    } for alloc in allocations]
    
    return jsonify({
        'container_id': container_id,
        'total_items': sum(item['quantity'] for item in item_list),
        'items': item_list
    })

@app.route('/api/container/<container_id>/delete', methods=['POST'])
def delete_container(container_id):
    """Deletes an entire location container and clears out all of its nested inventory items."""
    try:
        # Find all allocations mapped to this specific pallet/shelf
        allocations = PhysicalAllocation.query.filter_by(container_id=container_id).all()
        
        for alloc in allocations:
            db.session.delete(alloc)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Container {container_id} completely removed from ledger.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route('/api/model/,int:model_id>/delete', methods=['POST'])
def delete_model(model_id):
    """Deletes an equipment model profile and clears out all linked allocations"""
    try:
        model = EquipmentModel.query.get_or_404(model_id)

        for alloc in model.allocations:
            db.session.delete(alloc)

        db.session.delete(model)
        db.session.commit()

        return jsonify({'success': True, 'message': f'Model profile removed from database.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route('/api/containers/unique', methods=['GET'])
def get_unique_containers():
    """Fetches a real-time list of all unique storage container IDs active in the ledger."""
    # Queries the database for all unique container IDs, sorted alphabetically
    records = db.session.query(PhysicalAllocation.container_id).distinct().order_by(PhysicalAllocation.container_id).all()
    container_ids = [r[0] for r in records]
    return jsonify(container_ids)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_database_if_empty()
    app.run(debug=True, port=5000)