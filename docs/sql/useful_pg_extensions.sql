--- useful extensions for QS
 CREATE EXTENSION hstore
  SCHEMA public
  VERSION "1.8";

CREATE EXTENSION "uuid-ossp"
  SCHEMA public
  VERSION "1.1";

 CREATE EXTENSION unaccent
  SCHEMA public
  VERSION "1.1";

 CREATE EXTENSION tablefunc
  SCHEMA public
  VERSION "1.0";

 CREATE EXTENSION pgcrypto
  SCHEMA public
  VERSION "1.3";

 CREATE EXTENSION fuzzystrmatch
  SCHEMA public
  VERSION "1.1";

 CREATE EXTENSION btree_gist
  SCHEMA public
  VERSION "1.6";

 CREATE EXTENSION btree_gin
  SCHEMA public
  VERSION "1.3";
