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