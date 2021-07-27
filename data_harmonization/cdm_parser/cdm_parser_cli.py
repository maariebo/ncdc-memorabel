import click
import os
from utils import export_config, import_config, run_command
from constants import *
from cdm_builder import *
from parser import parse_csv_mapping
from parse_dataset import DataParser
from postgres_manager import PostgresManager

@click.group()
def cli():
    click.echo('OMOP parser CLI')

@cli.command(help='Set up the configurations when using the CLI without docker.')
@click.option('--user', prompt=True)
@click.option('--password', prompt=True, hide_input=True)
@click.option('--host', prompt=True)
@click.option('--port', prompt=True)
@click.option('--database-name', prompt=True, default=DB_DEFAULT_NAME)
@click.option('--vocabulary-path', prompt=True, default=VOCABULARY_DEFAULT_PATH)
@click.option('--destination-mapping', prompt=True, default=DESTINATION_MAPPING_DEFAULT_PATH)
@click.option('--source-mapping', prompt=True, default=SOURCE_MAPPING_DEFAULT_PATH)
@click.option('--dataset', prompt=True)
def set_up(user, password, host, port, database_name, vocabulary_path, destination_mapping, source_mapping, dataset):
    """ Set up the configurations needed.
    """
    configurations = {
        DB_USER: user,
        DB_PASSWORD: password,
        DB_HOST: host,
        DB_PORT: port,
        DB_DATABASE: database_name,
        VOCABULARY_PATH: vocabulary_path,
        DESTINATION_MAPPING_PATH: destination_mapping,
        SOURCE_MAPPING_PATH: source_mapping,
        DATASET_PATH: dataset
    }
    export_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION, configurations)

@cli.command(help='Set up the CDM database')
@click.option('--insert-voc/--no-insert-voc', default=False, type=bool)
@click.option('--sequence-start', default=1, type=int)
def set_db(insert_voc, sequence_start):
    """ Set up the CDM database:
        * Create new database with the CDM schema;
        * Create the sequences used to obtain de id's (a start value
        can be provided, may be useful in a case where data from multiple
        data sources will be parsed and used in the same database)
        * Optionally: Insert the vocabulary if available;
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    create_database()
    with PostgresManager() as pg:
        set_schema(pg)
        create_sequences(pg, sequence_start)
        if insert_voc and VOCABULARY_PATH in os.environ:
            insert_vocabulary(pg)

@cli.command()
def insert_constraints():
    """ Set the CDM primary keys and constraints.
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    with PostgresManager() as pg:
        set_constraints(pg)

@cli.command(help='Parse the dataset and populate the OMOP CDM database.')
@click.option('--cohort-name', prompt=True)
@click.option('--cohort-location')
@click.option('--start', default=0, type=int)
@click.option('--limit', default=-1, type=int)
#@click.option('--drop-temp-tables', default=True, type=bool)
def parse_data(cohort_name, cohort_location, start, limit):
    """ Parse the source dataset and populate the CDM database.
        
        Important: One or more temporary tables will be created to store information only required
        during this process. By default, these tables will be dropped by the end. There is an
        option to avoid dropping these tables that may be useful in cases with multiple files.
        Nonetheless, in any case, the tables should not exist after parsing all the data and 
        it's recommended to manually check if all tables with prefix 'temp' were removed successfully.
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    destination_mapping = parse_csv_mapping(os.getenv(DESTINATION_MAPPING_PATH))
    source_mapping = parse_csv_mapping(os.getenv(SOURCE_MAPPING_PATH))

    # TODO: create the statements and commit them in batches
    with PostgresManager() as pg:
        # Insert the cohort information
        # If the cohort is already in the DB it'll only retrieve the id
        cohort_id = None
        if cohort_name:
            location_id = insert_location(cohort_location if cohort_location else cohort_name, pg)
            cohort_id = insert_cohort(cohort_name, location_id, pg)

        # Create the necessary temporary table
        create_id_table(pg)

        # Parse the dataset
        parser = DataParser(
            os.getenv(DATASET_PATH),
            source_mapping,
            destination_mapping,
            cohort_id,
            pg
        )
        parser.parse_dataset(start, limit)

        # Dropping the temporary tables
        # if drop_temp_tables:
        #    pg.drop_table(ID_TABLE)

@cli.command()
def report():
    """ Returns information that can be use for quality control.
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    with PostgresManager() as pg:
        persons = get_number_of_persons(pg)
        if persons:
            for persons_by_gender in persons:
                print(f'Gender: {persons_by_gender[0]} - Count: {persons_by_gender[1]}; '
                    + f'Max/Min/Avg Year of Birth: {persons_by_gender[2]}/{persons_by_gender[3]}/{round(persons_by_gender[4], 2)};')
        entry_count = count_entries(pg)
        print(f'{entry_count[0]} observations, {entry_count[1]} measurements, {entry_count[2]} conditions')

# Possibility to use allow_extra_args and make it more 'open'
@click.option('-f', '--file', default='/mnt/data/omop_cdm_export.pgsql',
    help='Path for the output file')
@click.option('--data-only/--no-data-only', default=False, help='Export only data and not the DDL', type=bool)
@cli.command()
def export_db(file, data_only):
    """ Export the database to a file.
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    run_command(
        ['pg_dump', '-d', PostgresManager.get_database_uri(), '--data-only' if data_only else '', '-f', file],
        'Successfully exported the database.',
        'Failed to export the database.')

@click.option('-f', '--file', default='/mnt/data/omop_cdm_export.pgsql',
    help='Path for the file to import')
@click.option('--create-db/--no-create-db', default=False, help='Create the database?', type=bool)
@cli.command()
def import_db(file, create_db):
    """ Create and build a database from a file.
    """
    if DOCKER_ENV not in os.environ: import_config(DB_CONFIGURATION_PATH, DB_CONFIGURATION_SECTION)
    if create_db:
        create_database()
    run_command(
        ['psql', '-d', PostgresManager.get_database_uri(), '-f', file],
        'Successfully imported the database.',
        'Failed to import the database.')

if __name__ == '__main__':
    cli()