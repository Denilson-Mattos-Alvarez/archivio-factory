"""Prepara soltanto il PostgreSQL temporaneo della CI, con dati sintetici.

Nessun servizio viene avviato da questo script. Nomi occupati fanno fallire la
preparazione: non contiene DROP o modifiche a ruoli/schema già esistenti.
CREATEDB serve esclusivamente a creare il database di test Django della CI.
"""
import os
import sys
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import sql


DATABASE_CI = 'archivio_ci'
SCHEMA_CI = 'archivio_factory'
RUOLO_APP = 'archivio_app'
RUOLI_BROWSER = ('anon', 'authenticated')
HOST_CI = '127.0.0.1'
PORTA_CI = 5432


def verifica_ambiente_ci():
    if os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('ARCHIVIO_POSTGRES_CI') != '1':
        raise RuntimeError('Questo helper richiede GITHUB_ACTIONS=true e il flag del laboratorio PostgreSQL CI.')
    if os.getenv('ARCHIVIO_AMBIENTE') != 'sviluppo':
        raise RuntimeError('Il laboratorio CI richiede ambiente sviluppo.')
    url = urlsplit(os.getenv('DATABASE_URL', ''))
    app_password = os.getenv('ARCHIVIO_CI_APP_PASSWORD', '')
    admin_password = os.getenv('ARCHIVIO_CI_ADMIN_PASSWORD', '')
    if not app_password or not admin_password:
        raise RuntimeError('Mancano le password fittizie del laboratorio CI.')
    if (url.scheme != 'postgresql' or url.hostname not in {'localhost', HOST_CI} or
            url.port != PORTA_CI or url.path != '/' + DATABASE_CI or
            url.username != RUOLO_APP or url.password != app_password or
            parse_qs(url.query) != {'sslmode': ['disable']}):
        raise RuntimeError('DATABASE_URL deve indicare esclusivamente archivio_ci sul loopback della CI.')
    return admin_password, app_password


def connessione_admin(database, password):
    if database not in {DATABASE_CI, 'template1'}:
        raise RuntimeError('Database non autorizzato per il laboratorio CI.')
    return psycopg.connect(host=HOST_CI, port=PORTA_CI, dbname=database,
                          user='postgres', password=password, sslmode='disable',
                          connect_timeout=10, autocommit=True)


def prepara_postgres():
    admin_password, app_password = verifica_ambiente_ci()
    with connessione_admin(DATABASE_CI, admin_password) as connection:
        major_version = int(connection.execute('SHOW server_version_num').fetchone()[0]) // 10000
        if major_version != 16 or connection.execute('SELECT current_database()').fetchone()[0] != DATABASE_CI:
            raise RuntimeError('Il servizio deve essere PostgreSQL 16 nel database archivio_ci.')
        occupied = connection.execute('SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)',
                                      ([RUOLO_APP, *RUOLI_BROWSER],)).fetchall()
        if occupied:
            raise RuntimeError('Nome di ruolo già occupato: usare un servizio CI nuovo, senza riutilizzare ruoli esistenti.')
        if connection.execute('SELECT to_regnamespace(%s)', (SCHEMA_CI,)).fetchone()[0] is not None:
            raise RuntimeError('Schema CI già occupato: nessun oggetto esistente viene modificato.')
        # Verifica anche template1 prima di creare qualsiasi ruolo/schema.
        with connessione_admin('template1', admin_password) as template:
            if template.execute('SELECT to_regnamespace(%s)', (SCHEMA_CI,)).fetchone()[0] is not None:
                raise RuntimeError('Schema template1 già occupato: usare un servizio CI nuovo.')
        connection.execute(sql.SQL(
            'CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS'
        ).format(sql.Identifier(RUOLO_APP), sql.Literal(app_password)))
        for role in RUOLI_BROWSER:
            connection.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    for database in (DATABASE_CI, 'template1'):
        with connessione_admin(database, admin_password) as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}').format(
                sql.Identifier(SCHEMA_CI), sql.Identifier(RUOLO_APP)))
            connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC, {}, {}').format(
                sql.Identifier(SCHEMA_CI), *(sql.Identifier(role) for role in RUOLI_BROWSER)))
            for role in RUOLI_BROWSER:
                if connection.execute('SELECT has_schema_privilege(%s, %s, %s)',
                                      (role, SCHEMA_CI, 'USAGE')).fetchone()[0]:
                    raise RuntimeError('Privilegi inattesi sul nuovo schema del laboratorio CI.')
    print('PostgreSQL 16 CI pronto: ruolo applicativo distinto e schema privato ereditato dai database di test.')


if __name__ == '__main__':
    try:
        prepara_postgres()
    except Exception as error:
        # Nessun messaggio SQL o DSN nei log: potrebbero contenere password.
        print('Preparazione PostgreSQL CI fallita (' + type(error).__name__ +
              '): verificare flag CI, servizio loopback, credenziali fittizie e nomi liberi.', file=sys.stderr)
        raise SystemExit(1) from None
else:
    from django.db import connection
    from django.test.runner import DiscoverRunner

    class CollaudoPostgresRunner(DiscoverRunner):
        """La CI PostgreSQL fallisce anche se una prova viene saltata."""

        def setup_test_environment(self, **kwargs):
            verifica_ambiente_ci()
            configuration = connection.settings_dict
            if (connection.vendor != 'postgresql' or configuration['NAME'] != DATABASE_CI or
                    configuration['HOST'] not in {'localhost', HOST_CI} or
                    str(configuration['PORT']) != str(PORTA_CI) or configuration['USER'] != RUOLO_APP):
                raise RuntimeError('Il runner CI richiede il PostgreSQL temporaneo e il ruolo applicativo previsti.')
            return super().setup_test_environment(**kwargs)

        def suite_result(self, suite, result, **kwargs):
            skipped = len(result.skipped)
            if skipped:
                self.log(f'Collaudo PostgreSQL non accettato: {skipped} test saltati.', level=40)
            return super().suite_result(suite, result, **kwargs) + skipped
