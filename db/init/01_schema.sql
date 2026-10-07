SET NAMES utf8mb4;
-- Esquema de ejemplo: empresa manufacturera de equipo electro-médico
USE planta;

CREATE TABLE clientes (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  nombre      VARCHAR(120) NOT NULL,
  ciudad      VARCHAR(80)  NOT NULL,
  tipo        ENUM('hospital','clinica','distribuidor') NOT NULL
);

CREATE TABLE productos (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  sku             VARCHAR(30)  NOT NULL UNIQUE,
  nombre          VARCHAR(120) NOT NULL,
  categoria       VARCHAR(60)  NOT NULL,
  precio_unitario DECIMAL(10,2) NOT NULL,
  stock           INT NOT NULL DEFAULT 0,
  stock_minimo    INT NOT NULL DEFAULT 0
);

CREATE TABLE ordenes_produccion (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  producto_id     INT NOT NULL,
  cantidad        INT NOT NULL,
  estado          ENUM('planeada','en_proceso','terminada','retenida') NOT NULL,
  fecha_inicio    DATE NOT NULL,
  fecha_fin       DATE NULL,
  FOREIGN KEY (producto_id) REFERENCES productos(id)
);

CREATE TABLE pedidos (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  cliente_id      INT NOT NULL,
  producto_id     INT NOT NULL,
  cantidad        INT NOT NULL,
  fecha           DATE NOT NULL,
  estado          ENUM('abierto','enviado','entregado','cancelado') NOT NULL,
  FOREIGN KEY (cliente_id)  REFERENCES clientes(id),
  FOREIGN KEY (producto_id) REFERENCES productos(id)
);

CREATE TABLE inspecciones_calidad (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  orden_id        INT NOT NULL,
  fecha           DATE NOT NULL,
  resultado       ENUM('aprobada','rechazada') NOT NULL,
  defecto         VARCHAR(200) NULL,
  FOREIGN KEY (orden_id) REFERENCES ordenes_produccion(id)
);

-- Usuario de SOLO LECTURA para el agente SQL (capa 1 de seguridad).
-- Aunque el modelo genere un DELETE, MySQL lo rechaza por permisos.
CREATE USER IF NOT EXISTS 'agente_lectura'@'%' IDENTIFIED BY 'lectura_segura_123';
GRANT SELECT ON planta.* TO 'agente_lectura'@'%';
FLUSH PRIVILEGES;
