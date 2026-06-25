# Net Ops Spares Matric (umnet-infrastructure-inventory)
Web application designed to track network infrastructure, built specifically for the University of Michigan's ITS Infra Net Dir of Ops. This system maps inventory across physical storage arrays (pallets and shelves) within the departments spares room.

# Architecture
- Backend Framework: Python with Flask (RESTful routing engine)
- Database: SQLite managed via Flask-SQLAlchemy (relational tracking of models and distinct storage container profiles)
- Frontend: HTML5 & JavaScript (ES6+ asynchronous Fetch API)
- Styling Engine: Tailwind CSS via CDN

# Core Logic
** Warehouse assets are divided into two logical states: SKU Models and Physical Allocations **

The UI features an interconnected dual-pane system
- View by Model / SKU: Inspects a specific model to see every pallet or shelf it is held in
- View by Container: Uses the container ID to create a view of all models within the container

The backend automatically tracks duplicate entries concerning the Pallet / Shelf ID
- A new Storage Node ID creates a brand-new container matrix card
- A used Storage Node ID automatically combines existing inventory with newly inputted inventory

Database Schema
- HardwareModel: Tracks names, identifiers, and unique system part numbers/SKUs
- PhysicalAllocation: Maps HardwareModel IDs to cutom container_id fields, tracking quantity counts and managing state configurations

# Getting Started
** Prerequisites **
- Python 3.8+

** Installation & Run **
- Clone this repository into your local workspace directory
- Install dependencies: pip install flask flask_sqlalchemy
- Initialize and run the application server: python3 app.py
- Access the interface locally: http://127.0.0.1:5000/
