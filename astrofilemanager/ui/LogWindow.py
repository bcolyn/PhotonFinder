from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt


class LogWindow(QDialog):
    """
    A dialog window that displays log messages.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Log Messages")
        self.resize(600, 400)
        
        # Create layout
        layout = QVBoxLayout(self)
        
        # Create text area for log messages
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        # Create button layout
        button_layout = QHBoxLayout()
        
        # Create clear button
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(clear_button)
        
        # Create close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        
        # Add button layout to main layout
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def add_message(self, message):
        """
        Add a message to the log.
        """
        self.log_text.append(message)
    
    def clear_log(self):
        """
        Clear all messages from the log.
        """
        self.log_text.clear()