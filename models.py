from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class EquipmentModel(db.Model):
    __tablename__ = 'equipment_models'
    
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(120), nullable=False)
    sku = db.Column(db.String(80), unique=True, nullable=False)
    
    allocations = db.relationship('PhysicalAllocation', backref='hardware_profile', lazy=True, cascade='all, delete-orphan')
    serials = db.relationship('SerialNumber', backref='model', lazy=True, cascade='all, delete-orphan')

    @property
    def global_total(self):
        return sum(alloc.quantity for alloc in self.allocations)


class PhysicalAllocation(db.Model):
    __tablename__ = 'physical_allocations'
    
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('equipment_models.id'), nullable=False)
    container_id = db.Column(db.String(80), nullable=False)
    container_type = db.Column(db.String(80), nullable=True)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    
    serials = db.relationship('SerialNumber', backref='location', lazy=True, cascade='all, delete-orphan')


class SerialNumber(db.Model):
    __tablename__ = 'serial_numbers'
    
    id = db.Column(db.Integer, primary_key=True)
    # FIX: Explicitly set unique=False so default placeholder barcodes ('00000000') can be reused
    serial_code = db.Column(db.String(100), nullable=False, unique=False, index=True)
    
    model_id = db.Column(db.Integer, db.ForeignKey('equipment_models.id', ondelete='CASCADE'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('physical_allocations.id', ondelete='CASCADE'), nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'serial_code': self.serial_code,
            'model_id': self.model_id,
            'location_id': self.location_id,
            'container_id': self.location.container_id if self.location else "Unassigned"
        }