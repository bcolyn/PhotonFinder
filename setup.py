from pyqt_distutils.build_ui import build_ui
from setuptools import setup, find_packages

setup(
    name='PhotonFinder',
    version='1.0.0',
    packages=find_packages(),
    url='',
    license='',
    author='benny',
    author_email='',
    description='',
    cmdclass={
        'build_ui': build_ui
    }
)
