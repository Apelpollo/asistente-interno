SET NAMES utf8mb4;
-- Esquema de ejemplo: consultora energética y de gestión de proyectos BIM
CREATE DATABASE IF NOT EXISTS energia_bim CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE energia_bim;

CREATE TABLE edificios (
  id                     INT PRIMARY KEY,
  nombre                 VARCHAR(120) NOT NULL,
  tipo                   ENUM('oficinas','salud','comercial','industrial','educacion') NOT NULL,
  ciudad                 VARCHAR(80) NOT NULL,
  area_construida_m2     DECIMAL(10,2) NOT NULL,
  anio_construccion      INT NOT NULL,
  certificacion_objetivo VARCHAR(60) NULL
);

-- Espacios principales, como vendrían de una tabla de habitaciones exportada de Revit (modelo BIM)
CREATE TABLE espacios_bim (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  edificio_id  INT NOT NULL,
  nivel        VARCHAR(20) NOT NULL,
  nombre       VARCHAR(80) NOT NULL,
  uso          VARCHAR(40) NOT NULL,
  area_m2      DECIMAL(10,2) NOT NULL,
  FOREIGN KEY (edificio_id) REFERENCES edificios(id)
);

-- Consumo eléctrico mensual tomado de los recibos de CFE
CREATE TABLE consumos_energia (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  edificio_id  INT NOT NULL,
  mes          DATE NOT NULL,
  kwh          INT NOT NULL,
  costo_mxn    DECIMAL(12,2) NOT NULL,
  demanda_kw   DECIMAL(10,2) NOT NULL,
  FOREIGN KEY (edificio_id) REFERENCES edificios(id)
);

-- Usuario de SOLO LECTURA para el agente (capa 1 de seguridad)
CREATE USER IF NOT EXISTS 'agente_lectura'@'localhost' IDENTIFIED BY 'lectura_segura_123';
CREATE USER IF NOT EXISTS 'agente_lectura'@'%' IDENTIFIED BY 'lectura_segura_123';
GRANT SELECT ON energia_bim.* TO 'agente_lectura'@'localhost';
GRANT SELECT ON energia_bim.* TO 'agente_lectura'@'%';
FLUSH PRIVILEGES;