--used http://www.postgresqltutorial.com/import-csv-file-into-posgresql-table/
--used https://stackoverflow.com/questions/8584119/how-to-apply-a-function-to-each-element-of-an-array-column-in-postgres to help learn function synatax
--used how-to-find-duplicate-records-in-posgresql
--done to make sure that if the tables already exist they do not interfere
DROP TABLE IF EXISTS aliases;
DROP TABLE IF EXISTS tempAliases;
DROP TABLE IF EXISTS match;
DROP TABLE IF EXISTS wordtable;
DROP TABLE IF EXISTS wordtable1;
DROP TABLE IF EXISTS wordtable2;
DROP TABLE IF EXISTS word2entities;

CREATE TABLE tempAliases(
	alias1 text NOT NULL,
	alias2 text NOT NULL,
	entityid text NOT NULL
);
\copy tempAliases(alias1, alias2, entityid) FROM './Machine_Learning/nerData/cleansedData.txt' DELIMITER '|' CSV;
WITH TMP AS (SELECT alias1, alias2, entityid FROM tempAliases)
SELECT lower(alias1) AS alias1, lower(alias2) AS alias2, entityid INTO aliases FROM TMP;
WITH TMP1 AS (SELECT alias1 AS name, string_to_array(alias1, ' ') AS words, entityid FROM aliases),
TMP3 AS (SELECT generate_subscripts(words, 1) AS s, words AS words, name AS name, entityid FROM TMP1)
SELECT name, words[s] AS word, entityid INTO wordtable1 FROM TMP3 WHERE words[s] <> ''
UNION ALL
SELECT alias1 AS name, replace(alias1, ' ', '') AS word, entityid FROM aliases ORDER BY word ASC;
--repeat for the next wordtable
WITH TMP2 AS (SELECT alias2 AS name, string_to_array(alias2, ' ') AS words, entityid FROM aliases),
TMP4 AS (SELECT generate_subscripts(words, 1) AS s, words, name, entityid FROM TMP2)
SELECT name, words[s] AS word, entityid INTO WORDTABLE2 FROM TMP4 WHERE words[s] <> ''
UNION ALL
SELECT alias2 AS name, replace(alias2, ' ', '') AS word, entityid FROM aliases ORDER BY word ASC;

--creates a bucket of entity ids per word
WITH TMP AS 
(select alias1 AS name, string_to_array(alias1, ' ') AS words, entityid FROM aliases 
UNION 
select alias2 AS name, string_to_array(alias2, ' ') AS words, entityid FROM aliases), 
TMP1 as (select name, entityid, unnest(words) as word from TMP)
select word, array_agg(distinct entityid) as entities into word2entities from tmp1 where trim(word) <> '.' and trim(word) <> '' 
and trim(word) <> '.' and trim(word) <> ',' and length(word) > 1 group by word;
