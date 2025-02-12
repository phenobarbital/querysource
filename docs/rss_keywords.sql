CREATE SCHEMA IF NOT EXISTS rssapp AUTHORIZATION troc_pgdata;

DROP TABLE IF EXISTS rssapp.bundles_keywords;
CREATE TABLE rssapp.bundles_keywords (
    bundle_id character varying(17) NOT NULL,
    bundle_name character varying NOT NULL,
    keywords jsonb NOT NULL,
    vector jsonb,
    created_at timestamp DEFAULT now(),
    updated_at timestamp DEFAULT now(),
    CONSTRAINT pk_troc_rss_bundle PRIMARY KEY (bundle_id)
);
ALTER TABLE rssapp.bundles_keywords OWNER TO troc_pgdata;



INSERT INTO rssapp.bundles_keywords (bundle_id, bundle_name, keywords)
VALUES (
    '_GaB7Si3FWgUSYsgL',
    'Automobile',
    '[
        "automotive retail trends 2025",
        "future of car dealerships",
        "automotive retail innovations",
        "digital transformation in automotive retail",
        "latest trends in car sales",
        "customer experience in car dealerships",
        "enhancing customer service in auto sales",
        "automotive customer journey",
        "personalized car shopping experience",
        "improving dealership customer satisfaction",
        "dealership operations best practices",
        "managing car dealership inventory",
        "automotive dealership profitability strategies",
        "streamlining dealership operations",
        "training dealership staff",
        "electric vehicle sales trends North America",
        "selling EVs in the automotive retail market",
        "EV customer adoption strategies",
        "challenges in EV sales for dealerships",
        "marketing electric vehicles to customers",
        "online car shopping statistics 2025",
        "e-commerce platforms for car sales",
        "benefits of online car buying",
        "online car sales growth in North America",
        "how to sell cars online effectively",
        "automotive inventory management software",
        "best practices for dealership inventory control",
        "solving inventory shortages in auto retail",
        "used car inventory management strategies",
        "supply chain disruptions and inventory challenges",
        "automotive marketing strategies 2025",
        "digital marketing for car dealerships",
        "SEO for automotive retail",
        "social media marketing for car sales",
        "local SEO for car dealerships",
        "automotive e-commerce trends North America",
        "online marketplaces for car parts and accessories",
        "building an automotive e-commerce website",
        "challenges in automotive e-commerce",
        "retail technology in automotive e-commerce"
    ]'::jsonb
);
