# QFieldCloud Deployment

Notes on deploying QFieldCloud on cloud servers with `db` and `geodb` running docker containers (i.e. with the setup specified in `docker-compose.override.local.yml`).

## Ensure submodules are loaded:

```
git submodule init
git submodule update
```

## Update config file

```
cp .env.example .env
```

Update `STORAGE_ENDPOINT_URL` and `STORAGE_ENDPOINT_URL_EXTERNAL`. If using AWS S3 it should have the format: `https://s3.<region>.amazonaws.com`

Add host IP to `DJANGO_ALLOWED_HOSTS`. 

Add host domain name to `DJANGO_ALLOWED_HOSTS` and `QFIELDCLOUD_HOST` - this should be set with an A record in the domain registry.

Change `LETSENCRYPT_EMAIL`.

Set `LETSENCRYPT_STAGING` to 0 for production. 

Set `GEODB_PORT` to 5431.

Change passwords throughout. 

## Update local compose to use AWS S3

Use the `docker-compose.override.local.yml` to allow use of docker deployments of PostgreSQL database. 

Remove / comment the `createbuckets` service if you don't want to use AWS and want to use minio S3 storage installed on the server. 

```
ln -s docker-compose.override.local.yml docker-compose.override.yml
```

## Build


Build: 

```
docker-compose up -d --build
```

If making changes, use to avoid using cached images:

```
docker-compose up -d --build --force-recreate

# or for debugging

docker-compose up --build --force-recreate

```

Run django database migrations.
```
docker-compose exec app python manage.py migrate
```

Load static files:
```
docker-compose run app python manage.py collectstatic --noinput
```

Check status:

```
docker-compose exec app python manage.py status
```

Create superuser:

```
docker-compose run app python manage.py createsuperuser --username super_user --email <me@me.com>
```

## Add root certificate

```
sudo cp ./conf/nginx/certs/rootCA.pem /usr/local/share/ca-certificates/rootCA.crt
```

Trust new certificates:

```
sudo update-ca-certificates
```

## Setup Let's Encrypt

```
./scripts/init_letsencrypt.sh
```

## Logs directory
Create the directory for qfieldcloud logs and supervisor socket file

```
mkdir /var/local/qfieldcloud
```

## Create Fake Project

Using the django admin console, create an empyty init project which will allow creating cloud projects from within QGIS. 

# Email invites and user signup

If using gmail as a smtp server, set the following environment variables. 

```
ACCOUNT_EMAIL_VERIFICATION=optional
EMAIL_HOST=smtp.gmail.com
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_PORT=587
EMAIL_HOST_USER=<email>
EMAIL_HOST_PASSWORD=<password>
DEFAULT_FROM_EMAIL=<email>
```

### Configure a default landing page

This is what is shown to users after completing a signup or when logged in.

Create a `landing.html` in the `qfieldcloud/core/templates` directory:

```
touch landing.html
```

In `qfieldcloud/authentication/views.py` add the following:

```
from django.views.generic import TemplateView

# other code here

class Landing(TemplateView):
    template_name = 'landing.html'
```

Add `landing.html` to the urls in `qfieldcloud/urls.py`

```
path("auth/", include("rest_framework.urls")),
path("accounts/", include("allauth.urls")),
path("landing/", auth_views.Landing.as_view()), ### THIS IS THE PATH TO ADD ###
re_path(r"^invitations/", include("invitations.urls", namespace="invitations")),
```

Finally, in `qfieldcloud/settings.py` add:

```
LOGIN_REDIRECT_URL = "/landing/"
``` 

This will redirect this user to `landing.html` after signup or successful login. 

