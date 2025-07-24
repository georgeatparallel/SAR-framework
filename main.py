
import os
import logging
from app import create_app
import random

# Configure logging
logging.basicConfig(filename='app.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')

app = create_app(os.getenv('FLASK_CONFIG') or 'default')

port_id = random.randint(1000, 9999)

if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', port_id)))
