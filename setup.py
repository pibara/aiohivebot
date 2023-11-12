from setuptools import setup, find_packages

setup(
    name='aiohivebot',
    version='0.2.0',
    description="Asynchonous Python client library for the HIVE blockchain",
    long_description="An async python library for writing bots and DApp backends for the HIVE blockchain",
    author='Rob Meijer',
    author_email='pibara@gmail.com',
    url='https://github.com/pibara/aiohivebot',
    license='BSD',
    include_package_data=True,
    zip_safe=False,
    python_requires='>=3.9',
    keywords='hive blockchain jsonrpc',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    install_requires=["httpx", "average", "dateutils"],
    packages=find_packages(),
    package_data={'aiohivebot': ['**/*.json']}
)
