from pyqt_distutils.build_ui import build_ui
from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand

class PyTest(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        import pytest
        pytest.main(self.test_args)

setup(
    name='astrofilemanager',
    version='1.0.0',
    packages=find_packages(),
    url='',
    license='',
    author='benny',
    author_email='',
    description='',
    cmdclass={
        'build_ui': build_ui,
        'test': PyTest,
    },
    tests_require=['pytest', 'pytest-qt', 'pytest-mock'],
)
