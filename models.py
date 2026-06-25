from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class EquipmentModel(db.Model):
    __tablename__ = 'equipment_models'
    
    id = db.Column(db.Integer, primary_key=True)
    model_name = db.Column(db.String(100), nullable=False, unique=True)
    sku = db.Column(db.String(50), nullable=False, unique=True)
    
    # Relationship to grab all physical storage locations tracking this model
    allocations = db.relationship('PhysicalAllocation', backref='hardware_profile', lazy=True, cascade="all, delete-orphan")

    @property
    def global_total(self):
        """Dynamically sums the total count across all physical locations."""
        return sum(allocation.quantity for allocation in self.allocations)


class PhysicalAllocation(db.Model):
    __tablename__ = 'physical_allocations'
    
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('equipment_models.id'), nullable=False)
    
    container_id = db.Column(db.String(50), nullable=False)  # e.g., 'PALLET-04', 'SHELF-B3'
    container_type = db.Column(db.String(20), nullable=False) # 'Pallet Array' or 'Storage Shelf'
    quantity = db.Column(db.Integer, default=0, nullable=False)