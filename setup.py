import os
from setuptools import find_packages, setup

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(name="mqtttools",
      version="0.1.0",
      description="MQTT Home Automation Tools",
      longdescription=read('README.md'),
      author="Chris Burn",
      author_email='chrisburn@fastmail.net',
      platforms=["any"],
      classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: No Input/Output (Daemon)",
        "Framework :: Twisted",
        "Programming Language :: Python",
        "Topic :: Home Automation"
      ],
      install_requires = ['Twisted', 'twisted-mqtt'],
      license = "GPL-3.0",
)
