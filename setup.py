#!/usr/bin/env python
from setuptools import setup, find_packages

setup(
    name='jsb',
    version='0.0.1',
    author='Vlad Okhrimenko',
    author_email='vokhrimenko@mirantis.com',
    url='https://github.com/vladryk/bridge',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'jsb = jsb.runner:main',
        ],
    },
)
