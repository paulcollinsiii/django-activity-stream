Installation
============

Installation is easy using ``pip`` and the only requirement is a recent version of Django.

.. code-block:: bash

    $ pip install django-activity-stream

or get it from source

.. code-block:: bash

    $ pip install git+https://github.com/justquick/django-activity-stream.git#egg=actstream

Then to add the Django Activity Stream to your project add the app ``actstream`` to your ``INSTALLED_APPS`` and urlconf.

The app should go somewhere after all the apps that are going to be generating activities like ``django.contrib.auth``::

    INSTALLED_APPS = (
        'django.contrib.auth',
        ...
        'actstream',
        ...
    )

Add the activity urls to your urlconf::

    urlpatterns = patterns('',
        ...
        ('^activity/', include('actstream.urls')),
        ...
    )

If you have `South <http://south.aeracode.org/>`_ installed you have to migrate actstream tables

.. code-block:: bash

    $ django-admin.py migrate actstream

If you want to use custom data on your actions, then make sure you have `django-jsonfield <https://github.com/bradjasper/django-jsonfield/>`_ installed::

.. code-block:: bash

    $ pip install django-jsonfield

You can learn more at :ref:`custom-data`
