import os
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
from models import db, EquipmentModel, PhysicalAllocation, SerialNumber

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///netinfra_warehouse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def seed_database_if_empty():
    """Seed initial database records if equipment matrix is empty."""
    if EquipmentModel.query.count() == 0:
        cisco = EquipmentModel(model_name="Cisco Catalyst 9300 48P", sku="CISCO-C9300-48P")
        juniper = EquipmentModel(model_name="Juniper EX3300 24T", sku="JNPR-EX3300-24T")
        
        db.session.add_all([cisco, juniper])
        db.session.commit()
        
        loc1 = PhysicalAllocation(model_id=cisco.id, container_id="PALLET-04", container_type="Pallet Array", quantity=0)
        loc2 = PhysicalAllocation(model_id=cisco.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=0)
        loc3 = PhysicalAllocation(model_id=juniper.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=0)
        
        db.session.add_all([loc1, loc2, loc3])
        db.session.commit()

def format_serial(s):
    """Format a SerialNumber model instance into a dictionary."""
    return {
        'id': s.id,
        'serial_code': s.serial_code,
        'location_id': s.location_id,
        'container_id': s.location.container_id if s.location else 'Unassigned',
        'container_type': s.location.container_type if s.location else None
    }

def extract_brand(model_name):
    """Extract brand name from model name string (defaults to generic if absent)."""
    if not model_name:
        return "Generic / Other"
    parts = model_name.strip().split()
    return parts[0] if parts else "Generic / Other"

@app.route('/')
def dashboard():
    """Render main warehouse inventory dashboard."""
    catalog = EquipmentModel.query.all()
    selected_model = catalog[0] if catalog else None
    return render_template('dashboard.html', catalog=catalog, selected_model=selected_model)


@app.route('/api/model/<int:model_id>/locations', methods=['GET'])
def get_model_locations(model_id):
    """Retrieve physical allocation locations for a given equipment model."""
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


@app.route('/api/allocation/<int:alloc_id>/serials', methods=['GET'])
def get_allocation_serials(alloc_id):
    """Fetch all serial numbers associated with a specific allocation container."""
    serials = SerialNumber.query.filter_by(location_id=alloc_id).all()
    return jsonify({
        'success': True, 
        'serials': [format_serial(s) for s in serials]
    })

@app.route('/api/serials/search', methods=['GET'])
def search_serial():
    """Search registered serial numbers by barcode string."""
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': True, 'results': []})

    serials = SerialNumber.query.filter(
        SerialNumber.serial_code.ilike(f"%{query}%")
    ).all()

    results = []
    for s in serials:
        results.append({
            'serial_id': s.id,
            'serial_code': s.serial_code,
            'model_id': s.model_id,
            'model_name': s.model.model_name if s.model else 'Unknown',
            'sku': s.model.sku if s.model else 'N/A',
            'container_id': s.location.container_id if s.location else 'Unassigned',
            'container_type': s.location.container_type if s.location else None
        })

    return jsonify({'success': True, 'results': results})


@app.route('/api/allocation/<int:alloc_id>/update', methods=['POST'])
def update_quantity(alloc_id):
    """Increment or decrement staged quantity and synchronize serial records."""
    data = request.get_json() or {}
    action = data.get('action')
    
    allocation = PhysicalAllocation.query.get_or_404(alloc_id)
    
    if action == 'increment':
        raw_code = data.get('serial_code', '').strip()
        serial_code = raw_code.upper() if raw_code else f"SN-{allocation.id}-{allocation.quantity + 1}"
        
        allocation.quantity += 1
        
        new_serial = SerialNumber(
            serial_code=serial_code,
            model_id=allocation.model_id,
            location_id=allocation.id
        )
        db.session.add(new_serial)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'new_quantity': allocation.quantity,
            'new_global_total': allocation.hardware_profile.global_total
        })
        
    elif action == 'decrement':
        serial_id_to_remove = data.get('serial_id')
        
        if allocation.quantity <= 0:
            return jsonify({'success': False, 'error': 'Quantity is already at zero.'}), 400
            
        allocation.quantity -= 1
        
        if serial_id_to_remove:
            target_serial = SerialNumber.query.get(serial_id_to_remove)
            if target_serial:
                db.session.delete(target_serial)
        else:
            fallback_serial = SerialNumber.query.filter_by(location_id=allocation.id).first()
            if fallback_serial:
                db.session.delete(fallback_serial)
                
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'new_quantity': allocation.quantity,
            'new_global_total': allocation.hardware_profile.global_total
        })

    return jsonify({'success': False, 'error': 'Invalid request parameters'}), 400

@app.route('/catalog/add', methods=['POST'])
def add_new_sku():
    """Register a new hardware model SKU entry in the catalog."""
    name = request.form.get('model_name')
    sku = request.form.get('sku').strip().upper() if request.form.get('sku') else None
    
    if name and sku:
        existing_model = EquipmentModel.query.filter_by(sku=sku).first()
        if existing_model:
            return redirect(url_for('dashboard'))
        
        new_model = EquipmentModel(model_name=name, sku=sku)
        db.session.add(new_model)
        db.session.commit()
        
    return redirect(url_for('dashboard'))


@app.route('/location/assign', methods=['POST'])
def assign_routing_destination():
    """Assign an equipment model to a physical container location."""
    model_id = request.form.get('active_model_id')
    container_id = request.form.get('container_id', '').strip().upper()
    container_type = request.form.get('container_type')
    try:
        initial_qty = int(request.form.get('initial_qty', 0))
    except ValueError:
        initial_qty = 0
    
    if model_id and container_id:
        existing = PhysicalAllocation.query.filter_by(model_id=model_id, container_id=container_id).first()
        
        target_alloc = existing
        if existing:
            existing.quantity += initial_qty
        else:
            target_alloc = PhysicalAllocation(
                model_id=model_id,
                container_id=container_id,
                container_type=container_type,
                quantity=initial_qty
            )
            db.session.add(target_alloc)
        
        db.session.commit()

        if initial_qty > 0:
            existing_serials_count = SerialNumber.query.filter_by(location_id=target_alloc.id).count()
            for i in range(initial_qty):
                auto_sn = f"{container_id}-{target_alloc.id}-{existing_serials_count + i + 1}"
                db.session.add(SerialNumber(
                    serial_code=auto_sn,
                    model_id=model_id,
                    location_id=target_alloc.id
                ))
            db.session.commit()
        
    return redirect(url_for('dashboard'))

@app.route('/api/container/<container_id>/items', methods=['GET'])
def get_container_items(container_id):
    """Retrieve all models and quantities assigned to a container."""
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
    """Purge a physical container and all associated allocations."""
    try:
        allocations = PhysicalAllocation.query.filter_by(container_id=container_id).all()
        for alloc in allocations:
            db.session.delete(alloc)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Container {container_id} removed.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/model/<int:model_id>/delete', methods=['POST'])
def delete_model(model_id):
    """Delete a hardware model catalog entry."""
    try:
        model = EquipmentModel.query.get_or_404(model_id)
        db.session.delete(model)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Model profile removed.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/containers/unique', methods=['GET'])
def get_unique_containers():
    """List distinct container identifiers registered in warehouse."""
    records = db.session.query(PhysicalAllocation.container_id).distinct().order_by(PhysicalAllocation.container_id).all()
    container_ids = [r[0] for r in records]
    return jsonify(container_ids)

@app.route('/api/serials/add', methods=['POST'])
def add_serial():
    """Register a new serial barcode to a specific container allocation."""
    data = request.get_json() or {}
    code = data.get('serial_code', '').strip().upper()
    model_id = data.get('model_id')
    location_id = data.get('location_id')

    if not code or not model_id or not location_id:
        return jsonify({'success': False, 'error': 'Missing serial code, model ID, or location ID'}), 400

    existing = SerialNumber.query.filter_by(serial_code=code).first()
    if existing:
        return jsonify({'success': False, 'error': 'Serial number already exists!'}), 400

    allocation = PhysicalAllocation.query.get(location_id)
    if not allocation:
        return jsonify({'success': False, 'error': 'Allocation/Container not found'}), 404

    new_serial = SerialNumber(serial_code=code, model_id=model_id, location_id=location_id)
    allocation.quantity += 1
    
    db.session.add(new_serial)
    db.session.commit()

    return jsonify({'success': True, 'serial': format_serial(new_serial)})


@app.route('/api/serials/<int:serial_id>/delete', methods=['POST', 'DELETE'])
def delete_serial(serial_id):
    """Delete a serial number and update container quantity."""
    try:
        serial = SerialNumber.query.get_or_404(serial_id)
        
        if serial.location and serial.location.quantity > 0:
            serial.location.quantity -= 1

        db.session.delete(serial)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Serial number deleted.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/model/<int:model_id>/serials', methods=['GET'])
def get_model_serials(model_id):
    """Get all serial numbers assigned to a model."""
    serials = SerialNumber.query.filter_by(model_id=model_id).all()
    return jsonify({'success': True, 'serials': [format_serial(s) for s in serials]})


@app.route('/api/container/<container_id>/serials', methods=['GET'])
def get_container_serials(container_id):
    """Get all serial numbers located inside a given container."""
    serials = SerialNumber.query.join(PhysicalAllocation).filter(PhysicalAllocation.container_id == container_id).all()
    return jsonify({'success': True, 'serials': [format_serial(s) for s in serials]})

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    """Export complete warehouse inventory grouped by container as CSV download."""
    try:
        allocations = PhysicalAllocation.query.order_by(PhysicalAllocation.container_id).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Container Node", "Container Type", "Brand / Vendor", "Model Name", "SKU / Part Number", "Serial Code"])

        for alloc in allocations:
            serials = SerialNumber.query.filter_by(location_id=alloc.id).all()
            model = alloc.hardware_profile
            brand = extract_brand(model.model_name if model else '')
            model_name = model.model_name if model else 'Unknown Model'
            sku = model.sku if model else 'N/A'

            # Export registered serial numbers
            for s in serials:
                code = s.serial_code if s.serial_code and s.serial_code.strip() else '00000000'
                writer.writerow([alloc.container_id, alloc.container_type or 'N/A', brand, model_name, sku, code])

            # Export remaining unassigned units as fallback '00000000'
            remaining_qty = max(0, alloc.quantity - len(serials))
            for _ in range(remaining_qty):
                writer.writerow([alloc.container_id, alloc.container_type or 'N/A', brand, model_name, sku, '00000000'])

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=network_spares_inventory.csv"}
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/export/google-sheets', methods=['POST'])
def export_google_sheets():
    """Export container inventory data to Google Sheets or provide 1-click clipboard paste."""
    try:
        allocations = PhysicalAllocation.query.order_by(PhysicalAllocation.container_id).all()
        
        sheet_data = []
        for alloc in allocations:
            serials = SerialNumber.query.filter_by(location_id=alloc.id).all()
            model = alloc.hardware_profile
            brand = extract_brand(model.model_name if model else '')
            model_name = model.model_name if model else 'Unknown Model'
            sku = model.sku if model else 'N/A'

            unit_records = []
            for s in serials:
                code = s.serial_code if s.serial_code and s.serial_code.strip() else '00000000'
                unit_records.append({
                    'brand': brand,
                    'model_name': model_name,
                    'sku': sku,
                    'serial_code': code
                })

            remaining_qty = max(0, alloc.quantity - len(serials))
            for _ in range(remaining_qty):
                unit_records.append({
                    'brand': brand,
                    'model_name': model_name,
                    'sku': sku,
                    'serial_code': '00000000'
                })

            if unit_records:
                sheet_data.append({
                    'container_id': alloc.container_id,
                    'container_type': alloc.container_type or 'N/A',
                    'units': unit_records
                })

        tsv_rows = ["Container Node\tContainer Type\tBrand / Vendor\tModel Name\tSKU / Part Number\tSerial Code"]
        for group in sheet_data:
            for u in group['units']:
                tsv_rows.append(f"{group['container_id']}\t{group['container_type']}\t{u['brand']}\t{u['model_name']}\t{u['sku']}\t{u['serial_code']}")
        tsv_content = "\n".join(tsv_rows)

        # Try updating/creating real Google Sheet via gspread if credentials are provided
        creds_file = os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', 'service_account.json')
        if os.path.exists(creds_file):
            try:
                import gspread
                gc = gspread.service_account(filename=creds_file)
                sh = gc.create("Network Hardware Spares Inventory Matrix")
                worksheet = sh.get_worksheet(0)
                
                rows_to_append = [["Container Node", "Container Type", "Brand / Vendor", "Model Name", "SKU / Part Number", "Serial Code"]]
                for group in sheet_data:
                    for u in group['units']:
                        rows_to_append.append([
                            group['container_id'],
                            group['container_type'],
                            u['brand'],
                            u['model_name'],
                            u['sku'],
                            u['serial_code']
                        ])
                worksheet.update('A1', rows_to_append)
                
                sh.share('', perm_type='anyone', role='reader')
                return jsonify({
                    'success': True,
                    'spreadsheet_url': sh.url,
                    'message': 'Exported successfully to Google Sheets.'
                })
            except Exception as gspread_err:
                print(f"GSpread integration info: {gspread_err}")

        # Fallback return opening a brand new blank Google Sheet and offering direct CSV download + TSV clipboard copy
        return jsonify({
            'success': True,
            'is_fallback': True,
            'spreadsheet_url': 'https://sheets.new',
            'csv_download_url': '/api/export/csv',
            'tsv_data': tsv_content,
            'inventory_by_container': sheet_data,
            'message': 'Opening new blank Google Sheet and copying formatted inventory data to clipboard.'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_database_if_empty()
    app.run(debug=True, port=5000)