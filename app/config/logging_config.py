# app/config/logging_config.py

import logging
import sys
import os

def setup_logging():
    """
    Sets up the root logger for the entire application.
    This function should be called once at the start of the application.
    
    It configures logging to output to both a file ('logs/app.log') and the console.
    """
    try:
        # Define the directory for logs and create it if it doesn't exist
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            print(f"Created directory: {log_dir}")

        log_file_path = os.path.join(log_dir, "app.log")

        # Configure the logging system
        logging.basicConfig(
            # Set the minimum level of messages to handle
            level=logging.INFO,  
            
            # Define the format for log messages
            format='%(asctime)s [%(levelname)-8s] %(name)s - %(message)s',
            
            # Define the handlers (destinations) for the logs
            handlers=[
                # Handler to write logs to a file, with automatic rotation
                # Using a TimedRotatingFileHandler is robust for long-running apps
                logging.handlers.TimedRotatingFileHandler(
                    log_file_path, when="midnight", interval=1, backupCount=7
                ),
                
                # Handler to stream logs to the console
                logging.StreamHandler(sys.stdout)
            ]
        )

        # Log that configuration is complete
        # This will be the first message in your log files
        logging.info("Logging has been configured successfully.")

    except Exception as e:
        # Fallback to basic print if logging setup fails
        print(f"Error setting up logging: {e}")
        raise