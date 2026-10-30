"""Dialog for running image quality analysis on selected files."""

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from photonfinder.core import ApplicationContext
from photonfinder.models import File, SearchCriteria
from photonfinder.ui.BackgroundLoader import ImageAnalysisTask


def _analysed_a_light(task: ImageAnalysisTask) -> bool:
    """The quality diagnosis only covers plain LIGHT frames."""
    return any((image := getattr(file, 'image', None)) and (image.image_type or "").upper() == "LIGHT"
               for file, _ in task.analyzed_files)


class ImageAnalysisDialog(QDialog):
    """
    Shows progress while running image quality analysis.
    Auto-starts on show(). Emits analysis_complete(task) when all files are done.
    """
    analysis_complete = Signal(object)  # ImageAnalysisTask

    def __init__(self, context: ApplicationContext, files: list[File] | None,
                 search_criteria: SearchCriteria, parent=None,
                 task: ImageAnalysisTask | None = None, label: str | None = None,
                 on_diagnose: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Analyse Images")
        self.setMinimumSize(500, 350)
        self._task: ImageAnalysisTask | None = None

        layout = QVBoxLayout(self)
        n = len(files) if files else 0
        layout.addWidget(QLabel(
            label if label else f"Analysing {n} file(s)..." if n
            else "Analysing files matching current filter..."
        ))
        self._progress = QProgressBar()
        layout.addWidget(self._progress)
        self._log = QPlainTextEdit(readOnly=True)
        layout.addWidget(self._log)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._on_diagnose = on_diagnose
        self.diagnose_button = QPushButton("Diagnose Quality…")
        self.diagnose_button.setVisible(False)
        self.diagnose_button.clicked.connect(self._on_diagnose_clicked)
        btn_row.addWidget(self.diagnose_button)
        self._btn = QPushButton("Cancel")
        self._btn.clicked.connect(self._on_btn_clicked)
        btn_row.addWidget(self._btn)
        layout.addLayout(btn_row)

        self._started = False
        self._task = task or ImageAnalysisTask(
            context=context, search_criteria=search_criteria, files=files,
        )
        self._task.progress.connect(self._progress.setValue)
        self._task.total_found.connect(self._progress.setMaximum)
        self._task.message.connect(self._log.appendPlainText)
        self._task.finished.connect(self._on_finished)
        self._task.error.connect(self._on_error)

    def showEvent(self, event):
        super().showEvent(event)
        if self._task and not self._started:  # shown again after being minimised: already running
            self._started = True
            self._task.start()

    def _on_finished(self):
        if self._task is None:  # cancelled
            return
        task, self._task = self._task, None
        self._log.appendPlainText(f"\nDone: {len(task.analyzed_files)} file(s) analysed.")
        self._btn.setText("Close")
        self.diagnose_button.setVisible(self._on_diagnose is not None and _analysed_a_light(task))
        self.analysis_complete.emit(task)

    def _on_diagnose_clicked(self):
        self.close()
        self._on_diagnose()

    def _on_error(self, msg: str):
        self._log.appendPlainText(f"\nError: {msg}")
        self._btn.setText("Close")
        self._task = None

    def _on_btn_clicked(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self.close()

    def closeEvent(self, event):
        if self._task:
            self._task.cancel()
            self._task = None
        super().closeEvent(event)
