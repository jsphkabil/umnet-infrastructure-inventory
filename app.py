import os
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
from sqlalchemy import text
from models import db, EquipmentModel, PhysicalAllocation, SerialNumber

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///netinfra_warehouse.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def fix_legacy_sqlite_unique_constraint():
    """Detect and repair legacy SQLite table schema if serial_code has an old UNIQUE constraint on disk."""
    try:
        schema_info = db.session.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='serial_numbers'")).scalar()
        if schema_info and 'UNIQUE' in schema_info.upper() and 'SERIAL_CODE' in schema_info.upper():
            # Temporarily back up data, rebuild table without UNIQUE constraint, and restore records
            db.session.execute(text("CREATE TABLE IF NOT EXISTS serial_numbers_backup AS SELECT * FROM serial_numbers;"))
            db.session.execute(text("DROP TABLE serial_numbers;"))
            db.session.commit()
            
            # Re-create table using updated SQLAlchemy model schema
            db.create_all()
            
            db.session.execute(text("""
                INSERT INTO serial_numbers (id, serial_code, model_id, location_id)
                SELECT id, serial_code, model_id, location_id FROM serial_numbers_backup;
            """))
            db.session.execute(text("DROP TABLE serial_numbers_backup;"))
            db.session.commit()
    except Exception as e:
        db.session.rollback()

def seed_database_if_empty():
    """Seed initial database records if equipment matrix is empty."""
    if EquipmentModel.query.count() == 0:
        cisco = EquipmentModel(model_name="Cisco Catalyst 9300 48P", sku="CISCO-C9300-48P")
        juniper = EquipmentModel(model_name="Juniper EX3300 24T", sku="JNPR-EX3300-24T")
        
        db.session.add_all([cisco, juniper])
        db.session.commit()
        
        loc1 = PhysicalAllocation(model_id=cisco.id, container_id="PALLET-04", container_type="Pallet Array", quantity=2)
        loc2 = PhysicalAllocation(model_id=cisco.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=1)
        loc3 = PhysicalAllocation(model_id=juniper.id, container_id="SHELF-B3", container_type="Storage Shelf", quantity=2)
        
        db.session.add_all([loc1, loc2, loc3])
        db.session.commit()

        db.session.add_all([
            SerialNumber(serial_code="C9300-P04-01", model_id=cisco.id, location_id=loc1.id),
            SerialNumber(serial_code="C9300-P04-02", model_id=cisco.id, location_id=loc1.id),
            SerialNumber(serial_code="C9300-SB3-01", model_id=cisco.id, location_id=loc2.id),
            SerialNumber(serial_code="EX3300-SB3-01", model_id=juniper.id, location_id=loc3.id),
            SerialNumber(serial_code="EX3300-SB3-02", model_id=juniper.id, location_id=loc3.id),
        ])
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
    allocations = []
    
    for alloc in model.allocations:
        if alloc.quantity <= 0:
            db.session.delete(alloc)
        else:
            allocations.append({
                'id': alloc.id,
                'container_id': alloc.container_id,
                'container_type': alloc.container_type,
                'quantity': alloc.quantity
            })
    db.session.commit()
    
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
    model_id = allocation.model_id
    
    if action == 'increment':
        raw_code = data.get('serial_code', '').strip().upper()
        
        # Default to '00000000' if blank or explicitly set to '00000000'
        if not raw_code or raw_code == '00000000':
            serial_code = '00000000'
        else:
            existing = SerialNumber.query.filter(
                SerialNumber.serial_code == raw_code,
                SerialNumber.serial_code != '00000000'
            ).first()
            if existing:
                return jsonify({'success': False, 'error': f'Serial number {raw_code} already exists!'}), 400
            serial_code = raw_code
        
        allocation.quantity += 1
        
        new_serial = SerialNumber(
            serial_code=serial_code,
            model_id=allocation.model_id,
            location_id=allocation.id
        )
        db.session.add(new_serial)
        db.session.commit()
        
        model_obj = EquipmentModel.query.get(model_id)
        new_global = model_obj.global_total if model_obj else 0
        
        return jsonify({
            'success': True, 
            'new_quantity': allocation.quantity,
            'new_global_total': new_global,
            'model_id': model_id
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
        
        # Automatically purge allocation card if model quantity hits 0 in this container
        is_purged = allocation.quantity <= 0
        if is_purged:
            db.session.delete(allocation)

        db.session.commit()
        
        model_obj = EquipmentModel.query.get(model_id)
        new_global = model_obj.global_total if model_obj else 0
        
        return jsonify({
            'success': True, 
            'new_quantity': 0 if is_purged else allocation.quantity,
            'new_global_total': new_global,
            'model_id': model_id
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
        initial_qty = int(request.form.get('initial_qty', 1))
    except ValueError:
        initial_qty = 1
    
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
            for _ in range(initial_qty):
                db.session.add(SerialNumber(
                    serial_code='00000000',
                    model_id=model_id,
                    location_id=target_alloc.id
                ))
            db.session.commit()
        
    return redirect(url_for('dashboard'))

@app.route('/api/container/<container_id>/items', methods=['GET'])
def get_container_items(container_id):
    """Retrieve all models and quantities assigned to a container."""
    allocations = PhysicalAllocation.query.filter_by(container_id=container_id).all()
    
    item_list = []
    for alloc in allocations:
        if alloc.quantity <= 0:
            db.session.delete(alloc)
        else:
            item_list.append({
                'alloc_id': alloc.id,
                'model_id': alloc.model_id,
                'model_name': alloc.hardware_profile.model_name,
                'sku': alloc.hardware_profile.sku,
                'quantity': alloc.quantity
            })
    db.session.commit()
    
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
    """List distinct container identifiers registered in warehouse with non-zero units."""
    records = db.session.query(PhysicalAllocation.container_id).filter(PhysicalAllocation.quantity > 0).distinct().order_by(PhysicalAllocation.container_id).all()
    container_ids = [r[0] for r in records]
    return jsonify(container_ids)

@app.route('/api/serials/add', methods=['POST'])
def add_serial():
    """Register a new serial barcode to a specific container allocation."""
    data = request.get_json() or {}
    code = data.get('serial_code', '').strip().upper()
    if not code:
        code = '00000000'
        
    model_id = data.get('model_id')
    location_id = data.get('location_id')

    if not model_id or not location_id:
        return jsonify({'success': False, 'error': 'Missing model ID or location ID'}), 400

    if code != '00000000':
        existing = SerialNumber.query.filter(
            SerialNumber.serial_code == code,
            SerialNumber.serial_code != '00000000'
        ).first()
        if existing:
            return jsonify({'success': False, 'error': 'Serial number already exists!'}), 400

    allocation = PhysicalAllocation.query.get(location_id)
    if not allocation:
        return jsonify({'success': False, 'error': 'Allocation/Container not found'}), 404

    new_serial = SerialNumber(serial_code=code, model_id=model_id, location_id=location_id)
    allocation.quantity += 1
    
    db.session.add(new_serial)
    db.session.commit()

    model_obj = EquipmentModel.query.get(model_id)
    new_global = model_obj.global_total if model_obj else 0

    return jsonify({
        'success': True, 
        'serial': format_serial(new_serial),
        'model_id': model_id,
        'new_global_total': new_global
    })


@app.route('/api/serials/<int:serial_id>/delete', methods=['POST', 'DELETE'])
def delete_serial(serial_id):
    """Delete a serial number and update container quantity."""
    try:
        serial = SerialNumber.query.get_or_404(serial_id)
        alloc = serial.location
        model_id = serial.model_id
        
        if alloc and alloc.quantity > 0:
            alloc.quantity -= 1

        db.session.delete(serial)

        # Remove allocation if quantity drops to 0
        if alloc and alloc.quantity <= 0:
            db.session.delete(alloc)

        db.session.commit()

        model_obj = EquipmentModel.query.get(model_id)
        new_global = model_obj.global_total if model_obj else 0

        return jsonify({
            'success': True, 
            'message': 'Serial number deleted.',
            'model_id': model_id,
            'new_global_total': new_global
        })
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
    """Export complete warehouse inventory grouped by container as CSV/Excel download."""
    try:
        allocations = PhysicalAllocation.query.filter(PhysicalAllocation.quantity > 0).order_by(PhysicalAllocation.container_id).all()
        
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
    """Export container inventory data for Google Sheets and Excel download."""
    try:
        allocations = PhysicalAllocation.query.filter(PhysicalAllocation.quantity > 0).order_by(PhysicalAllocation.container_id).all()
        
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
                
                try:
                    worksheet.columns_auto_resize(0, 6)
                except Exception as resize_err:
                    print(f"Auto-resize info: {resize_err}")

                sh.share('', perm_type='anyone', role='reader')
                return jsonify({
                    'success': True,
                    'spreadsheet_url': sh.url,
                    'tsv_data': tsv_content,
                    'message': 'Exported successfully to Google Sheets.'
                })
            except Exception as gspread_err:
                print(f"GSpread integration info: {gspread_err}")

        return jsonify({
            'success': True,
            'is_fallback': True,
            'spreadsheet_url': 'https://sheets.new',
            'csv_download_url': '/api/export/csv',
            'tsv_data': tsv_content,
            'inventory_by_container': sheet_data,
            'message': 'Opening new blank Google Sheet, copying inventory data to clipboard, and downloading CSV/Excel spreadsheet.'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/import/csv', methods=['POST'])
def import_csv():
    """Import and restore inventory records from exported CSV/TSV file or raw pasted text."""
    try:
        csv_text = None
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            csv_text = file.read().decode('utf-8-sig', errors='replace')
        elif request.is_json:
            data = request.get_json() or {}
            csv_text = data.get('csv_content', '')
        else:
            csv_text = request.form.get('csv_content', '')

        if not csv_text or not csv_text.strip():
            return jsonify({'success': False, 'error': 'No CSV or TSV data provided.'}), 400

        # Reset all existing database records prior to restore population
        SerialNumber.query.delete()
        PhysicalAllocation.query.delete()
        EquipmentModel.query.delete()
        db.session.flush()

        sample = csv_text[:2048]
        delimiter = '\t' if '\t' in sample and sample.count('\t') > sample.count(',') else ','

        stream = io.StringIO(csv_text)
        reader = csv.reader(stream, delimiter=delimiter)

        headers = None
        rows_processed = 0
        models_created = 0
        containers_created = 0
        serials_created = 0

        model_cache = {}
        alloc_cache = {}
        used_serials = set()

        for row in reader:
            if not row or not any(row):
                continue

            row_str = " ".join(row).lower()
            if 'container' in row_str and ('model' in row_str or 'sku' in row_str):
                headers = [col.strip().lower() for col in row]
                continue

            container_id = "UNASSIGNED"
            container_type = "Storage Shelf"
            model_name = "Unknown Model"
            sku = "GENERIC-SKU"
            raw_serial_code = "00000000"

            if headers:
                col_map = {h: idx for idx, h in enumerate(headers)}
                for h_key, idx in col_map.items():
                    if idx < len(row):
                        val = row[idx].strip()
                        if 'container node' in h_key or 'container id' in h_key or h_key == 'container':
                            container_id = val or container_id
                        elif 'container type' in h_key or 'type' in h_key:
                            container_type = val or container_type
                        elif 'model name' in h_key or 'model' in h_key:
                            model_name = val or model_name
                        elif 'sku' in h_key or 'part number' in h_key:
                            sku = val or sku
                        elif 'serial' in h_key:
                            raw_serial_code = val or raw_serial_code
            else:
                if len(row) >= 1: container_id = row[0].strip() or container_id
                if len(row) >= 2: container_type = row[1].strip() or container_type
                if len(row) >= 4: model_name = row[3].strip() or model_name
                if len(row) >= 5: sku = row[4].strip() or sku
                if len(row) >= 6: raw_serial_code = row[5].strip() or raw_serial_code

            sku = sku.upper()
            container_id = container_id.upper()
            raw_serial_code = raw_serial_code.upper() if raw_serial_code else "00000000"

            if sku not in model_cache:
                model = EquipmentModel.query.filter_by(sku=sku).first()
                if not model:
                    model = EquipmentModel(model_name=model_name, sku=sku)
                    db.session.add(model)
                    db.session.flush()
                    models_created += 1
                model_cache[sku] = model
            else:
                model = model_cache[sku]

            alloc_key = (model.id, container_id)
            if alloc_key not in alloc_cache:
                alloc = PhysicalAllocation.query.filter_by(model_id=model.id, container_id=container_id).first()
                if not alloc:
                    alloc = PhysicalAllocation(
                        model_id=model.id,
                        container_id=container_id,
                        container_type=container_type,
                        quantity=0
                    )
                    db.session.add(alloc)
                    db.session.flush()
                    containers_created += 1
                alloc_cache[alloc_key] = alloc
            else:
                alloc = alloc_cache[alloc_key]

            if not raw_serial_code or raw_serial_code == '00000000':
                final_sn = '00000000'
            else:
                final_sn = raw_serial_code
                used_serials.add(final_sn)

            new_sn = SerialNumber(
                serial_code=final_sn,
                model_id=model.id,
                location_id=alloc.id
            )
            db.session.add(new_sn)
            alloc.quantity += 1
            serials_created += 1
            rows_processed += 1

        db.session.commit()

        return jsonify({
            'success': True,
            'rows_processed': rows_processed,
            'models_created': models_created,
            'containers_created': containers_created,
            'serials_created': serials_created,
            'message': f'Successfully restored {rows_processed} items into the matrix ledger!'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        fix_legacy_sqlite_unique_constraint()
        seed_database_if_empty()
    app.run(debug=True, port=5000)