-- Create roles
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'omnivoice_admin') THEN
        CREATE ROLE omnivoice_admin WITH LOGIN PASSWORD 'admin_password_123';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'omnivoice_viewer') THEN
        CREATE ROLE omnivoice_viewer WITH LOGIN PASSWORD 'viewer_password_123';
    END IF;
END
$$;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE omnivoice_web TO omnivoice_admin;
GRANT ALL ON SCHEMA public TO omnivoice_admin;
GRANT CONNECT ON DATABASE omnivoice_web TO omnivoice_viewer;
GRANT USAGE ON SCHEMA public TO omnivoice_viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO omnivoice_viewer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO omnivoice_viewer;
