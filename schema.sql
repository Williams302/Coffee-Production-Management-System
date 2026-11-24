CREATE TABLE IF NOT EXISTS coffee_plots (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    area_hectares REAL NOT NULL CHECK (area_hectares > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS farm_activities (
    id INTEGER PRIMARY KEY,
    plot_id INTEGER NOT NULL REFERENCES coffee_plots(id),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    activity_date TEXT NOT NULL CHECK (date(activity_date, '+0 days') IS activity_date)
) STRICT;

CREATE TABLE IF NOT EXISTS harvests (
    id INTEGER PRIMARY KEY,
    plot_id INTEGER NOT NULL REFERENCES coffee_plots(id),
    harvest_date TEXT NOT NULL CHECK (date(harvest_date, '+0 days') IS harvest_date),
    weight_kg REAL NOT NULL CHECK (weight_kg > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    harvest_id INTEGER NOT NULL REFERENCES harvests(id),
    sale_date TEXT NOT NULL CHECK (date(sale_date, '+0 days') IS sale_date),
    weight_kg REAL NOT NULL CHECK (weight_kg > 0),
    revenue_ugx INTEGER NOT NULL CHECK (revenue_ugx >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS market_prices (
    id INTEGER PRIMARY KEY,
    market TEXT NOT NULL CHECK (length(trim(market)) > 0),
    coffee_type TEXT NOT NULL CHECK (length(trim(coffee_type)) > 0),
    price_date TEXT NOT NULL CHECK (date(price_date, '+0 days') IS price_date),
    price_ugx_per_kg INTEGER NOT NULL CHECK (price_ugx_per_kg > 0),
    UNIQUE (market, coffee_type, price_date)
) STRICT;
