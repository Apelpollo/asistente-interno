SET NAMES utf8mb4;
USE energia_bim;

INSERT INTO edificios VALUES
(1,'Torre Corporativa Norte','oficinas','Chihuahua',8500,2015,'LEED Gold'),
(2,'Hospital Regional Sur','salud','Chihuahua',12000,2008,NULL),
(3,'Plaza Comercial Juventud','comercial','Chihuahua',6200,2019,'LEED Silver'),
(4,'Nave Industrial Complejo','industrial','Ciudad Juárez',15000,2012,NULL),
(5,'Campus Educativo Tec','educacion','Chihuahua',9800,2005,'Cumplimiento NOM-008-ENER');

INSERT INTO espacios_bim (edificio_id,nivel,nombre,uso,area_m2) VALUES
(1,'PB','Lobby','Circulación',420),(1,'N1-N8','Oficinas abiertas','Oficina',6100),
(1,'N9','Site de datos','Técnico',180),(1,'Sótano','Cuarto de máquinas','Técnico',350),
(2,'PB','Urgencias','Salud',1800),(2,'N1','Quirófanos','Salud',950),
(2,'N2-N4','Hospitalización','Salud',6400),(2,'Sótano','Casa de máquinas','Técnico',700),
(3,'PB','Locales comerciales','Comercial',3900),(3,'PB','Food court','Comercial',1100),
(3,'Azotea','Equipos HVAC','Técnico',260),
(4,'PB','Nave de producción','Industrial',12500),(4,'Mezzanine','Oficinas','Oficina',900),
(4,'PB','Almacén','Almacén',1400),
(5,'Edif. A','Aulas','Educación',5200),(5,'Edif. B','Laboratorios','Educación',2100),
(5,'Edif. C','Biblioteca','Educación',1300);

-- 12 meses de 2025 por edificio: pico en verano (aire acondicionado) y ligero en invierno
INSERT INTO consumos_energia (edificio_id, mes, kwh, costo_mxn, demanda_kw)
SELECT b.id,
       DATE_ADD('2025-01-01', INTERVAL m.n MONTH),
       ROUND(b.kwh * CASE WHEN m.n BETWEEN 4 AND 8 THEN 1.35
                          WHEN m.n IN (0, 1, 11) THEN 1.10 ELSE 1.0 END),
       0, 0
FROM (SELECT 1 id, 120000 kwh UNION ALL SELECT 2, 310000 UNION ALL SELECT 3, 140000
      UNION ALL SELECT 4, 260000 UNION ALL SELECT 5, 90000) b
CROSS JOIN (SELECT 0 n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
      UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7
      UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11) m;

-- Anomalía intencional para la demo: pico en julio en la Plaza Comercial
UPDATE consumos_energia SET kwh = ROUND(kwh * 1.6) WHERE edificio_id = 3 AND mes = '2025-07-01';

-- Costo y demanda aproximados
UPDATE consumos_energia SET costo_mxn = kwh * 2.85, demanda_kw = ROUND(kwh / (730 * 0.55), 2);