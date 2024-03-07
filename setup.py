from setuptools import setup


with open('requirements.txt') as f:
    install_requires = f.read().strip().split('\n')
    
name = "check_run"

setup()
