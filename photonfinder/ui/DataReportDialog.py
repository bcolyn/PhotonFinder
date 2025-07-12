import logging
from datetime import datetime

from PySide6.QtWidgets import QDialog, QDialogButtonBox
from peewee import JOIN

from photonfinder.models import UsageReport, UsageReportLine, Image, File, SearchCriteria
from photonfinder.ui.BackgroundLoader import ProgressBackgroundTask
from photonfinder.ui.CreateDataReportDialog import CreateDataReportDialog
from photonfinder.ui.generated.DataReportDialog_ui import Ui_DataUsageReportDialog


class DataUsageReportTask(ProgressBackgroundTask):
    report_id: int | None

    def start(self, report_id: int | None):
        self.report_id = report_id
        self.run_in_thread(self._generate_report)

    def _generate_report(self):
        try:
            if self.report_id is None:
                # Regenerate all reports
                reports = list(UsageReport.select())
                total_reports = len(reports)
                self.total_found.emit(total_reports)

                for i, report in enumerate(reports):
                    if self.cancelled:
                        break

                    self.progress.emit(i)
                    self.message.emit(f"Regenerating report {i + 1}/{total_reports}: {report.name}")

                    self._generate_single_report(report)
            else:
                # Regenerate single report
                report_definition: UsageReport = UsageReport.get_by_id(self.report_id)
                self.total_found.emit(1)
                self.progress.emit(0)
                self.message.emit(f"Regenerating report: {report_definition.name}")
                self._generate_single_report(report_definition)

            self.finished.emit()

        except Exception as e:
            logging.error(f"Error generating report: {e}", exc_info=True)
            self.error.emit(str(e))

    def _generate_single_report(self, report_definition: UsageReport):
        """Generate a single report."""
        with self.context.database.atomic():
            self._clean_existing_report_lines(report_definition)
            integrations_criteria, lights_criteria = self._get_search_criteria(report_definition)
            integrations_query = self._create_integrations_query(integrations_criteria)
            self._process_integrations(report_definition, integrations_query, lights_criteria)
            self._update_report_timestamp(report_definition)

    def _clean_existing_report_lines(self, report_definition: UsageReport):
        """Delete existing UsageReportLine records for this report."""
        UsageReportLine.delete().where(UsageReportLine.report == report_definition).execute()

    def _get_search_criteria(self, report_definition: UsageReport):
        """Deserialize and return integrations and lights criteria."""
        integrations_criteria = SearchCriteria.from_json(report_definition.integrations_criteria)
        lights_criteria = SearchCriteria.from_json(report_definition.lights_criteria)
        return integrations_criteria, lights_criteria

    def _create_integrations_query(self, integrations_criteria: SearchCriteria):
        """Create and return query for integrations with non-null RA and DEC."""
        with self.context.database.bind_ctx([File, Image]):
            integrations_query = (Image
                                .select(Image, File)
                                .join(File, JOIN.INNER, on=(File.rowid == Image.file))
                                .where((Image.coord_ra.is_null(False)) & (Image.coord_dec.is_null(False))))

            integrations_query = Image.apply_search_criteria(integrations_query, integrations_criteria)
            return integrations_query

    def _process_integrations(self, report_definition: UsageReport, integrations_query, lights_criteria: SearchCriteria):
        """Process all integrations and create usage report lines."""
        total_integrations = integrations_query.count()
        self.total_found.emit(total_integrations)

        for i, integration in enumerate(integrations_query):
            if self.cancelled:
                break

            self.progress.emit(i)
            self.message.emit(f"Processing integration {i + 1}/{total_integrations}")

            self._process_single_integration(report_definition, integration, lights_criteria)

    def _process_single_integration(self, report_definition: UsageReport, integration, lights_criteria: SearchCriteria):
        """Process a single integration and create usage report lines for matching light files."""
        lights_search_criteria = self._create_lights_criteria(integration, lights_criteria, report_definition.coord_tolerance)
        lights_query = self._create_lights_query(lights_search_criteria)

        for light in lights_query:
            UsageReportLine.create(
                report=report_definition,
                light_file=light.file,
                integration_file=integration.file
            )

    def _create_lights_criteria(self, integration, lights_criteria: SearchCriteria, coord_tolerance: float) -> SearchCriteria:
        """Create a copy of lights criteria with coordinates from the integration."""
        lights_search_criteria = SearchCriteria.from_json(lights_criteria.to_json())
        lights_search_criteria.coord_ra = str(integration.coord_ra)
        lights_search_criteria.coord_dec = str(integration.coord_dec)
        lights_search_criteria.coord_radius = coord_tolerance
        return lights_search_criteria

    def _create_lights_query(self, lights_search_criteria: SearchCriteria):
        """Create and return query for light files matching the criteria."""
        lights_query = (Image
                       .select(Image, File)
                       .join(File, JOIN.INNER, on=(File.rowid == Image.file)))

        lights_query = Image.apply_search_criteria(lights_query, lights_search_criteria)
        return lights_query

    def _update_report_timestamp(self, report_definition: UsageReport):
        """Update the generated_at timestamp for the report."""
        report_definition.generated_at = datetime.now()
        report_definition.save()




class DataUsageReportDialog(QDialog, Ui_DataUsageReportDialog):

    def __init__(self, parent=None):
        super(DataUsageReportDialog, self).__init__(parent)
        self.setupUi(self)
        self.progressBar.setVisible(False)
        self._current_task = None
        self.connect_signals()
        self.disable_enable_controls()
        self.refresh_report_list()
        self.reports = list()
        self.update_generated_at()

    def connect_signals(self):
        self.comboBox.currentIndexChanged.connect(self.disable_enable_controls)
        self.comboBox.currentIndexChanged.connect(self.update_generated_at)
        self.create_button.clicked.connect(self.create_new_report)
        self.delete_button.clicked.connect(self.delete_current)
        self.regenerate_button.clicked.connect(self.regenerate_current)
        self.pushButton.clicked.connect(self.regenerate_all)

    def disable_enable_controls(self):
        index = self.comboBox.currentIndex()
        self.delete_button.setEnabled(index >= 0)
        self.regenerate_button.setEnabled(index >= 0)
        self.buttonBox.button(QDialogButtonBox.StandardButton.Save).setEnabled(self.tableWidget.rowCount() > 0)

    def create_new_report(self):
        main_window = self.parent()
        dialog = CreateDataReportDialog(main_window=main_window, parent=self)
        main_window.tabs_changed.connect(dialog.refresh_tab_lists)
        dialog.accepted.connect(self.refresh_report_list)
        dialog.show()

    def refresh_report_list(self):
        reports = UsageReport.select().execute()
        self.comboBox.clear()
        for report in reports:
            self.comboBox.addItem(report.name, report)
        self.disable_enable_controls()

    def update_generated_at(self):
        index = self.comboBox.currentIndex()
        if index >= 0:
            report = self.comboBox.itemData(index)
            if report and report.generated_at:
                self.generated_label.setText(
                    "Report generated at: " + report.generated_at.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.generated_label.setText("Report generated at: (never generated)")
        else:
            self.generated_label.setText("")

    def delete_current(self):
        index = self.comboBox.currentIndex()
        if index >= 0:
            report = self.comboBox.itemData(index)
            report.delete_instance()
            self.comboBox.removeItem(index)

    def regenerate_current(self):
        """Regenerate the currently selected report."""
        index = self.comboBox.currentIndex()
        if index >= 0:
            report = self.comboBox.itemData(index)
            self._start_report_generation(report.rowid)

    def regenerate_all(self):
        """Regenerate all reports."""
        self._start_report_generation(None)

    def _start_report_generation(self, report_id: int | None):
        """Start the report generation task."""
        # Get the application context from the parent window
        main_window = self.parent()
        if hasattr(main_window, 'context'):
            context = main_window.context
        else:
            # Fallback - try to get context from the main window's parent or other sources
            logging.error("Could not find application context")
            return

        # Create and start the task
        task = DataUsageReportTask(context)

        # Connect task signals to UI updates
        task.progress.connect(self.progressBar.setValue)
        task.total_found.connect(self.progressBar.setMaximum)
        task.message.connect(lambda msg: logging.info(f"Report generation: {msg}"))
        task.finished.connect(self._on_report_generation_finished)
        task.error.connect(self._on_report_generation_error)

        # Show progress bar and disable controls
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self._set_controls_enabled(False)

        # Start the task
        task.start(report_id)

        # Store reference to task to prevent garbage collection
        self._current_task = task

    def _on_report_generation_finished(self):
        """Handle completion of report generation."""
        self.progressBar.setVisible(False)
        self._set_controls_enabled(True)
        self.update_generated_at()
        self._current_task = None
        logging.info("Report generation completed successfully")

    def _on_report_generation_error(self, error_message: str):
        """Handle error during report generation."""
        self.progressBar.setVisible(False)
        self._set_controls_enabled(True)
        self._current_task = None
        logging.error(f"Report generation failed: {error_message}")

    def _set_controls_enabled(self, enabled: bool):
        """Enable or disable dialog controls."""
        self.comboBox.setEnabled(enabled)
        self.create_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled and self.comboBox.currentIndex() >= 0)
        self.regenerate_button.setEnabled(enabled and self.comboBox.currentIndex() >= 0)
        self.pushButton.setEnabled(enabled)
