===============================
Django send SMS using API(uz) (Tuzatilishlar olib borilmoqda... 🛠 )
===============================


Quick start 
------------

        1. Create a new project
        2. ``pip install django-sms-uz``, ``pip install djangorestframework`` and ``pip install requests``
        3. Add the following to your settings.py file: 
            ``INSTALLED_APPS = ( ... 'SMS', ... )``

           ``SMS_SETTINGS = {"SMS_URL": "http://91.204.239.44/broker-api/send","SMS_LOGIN": "LOGIN","SMS_PASSWORD": "PASSWORD"}``

            set in your settings.py file
        4. Add the following to your urls.py file:
           `` path('sms/', include('SMS.urls'))``
        5. Run ``python manage.py migrate`` to create the database tables
        6. Start your Django development server ``python manage.py runserver`` and go to http://localhost:8000/sms/send_sms/
        7. Enter your phone number and message and click send
        8. Check your terminal for a response and Django admin will show you if it was successful or not
        9. You could use call ``from SMS.sms_utils import SMS_Sender`` to get the SMS class and send sms using by the way ``SMS_Sender(phone_number, message).SendSmsOneContact()`` it returns a response

