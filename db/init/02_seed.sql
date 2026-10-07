SET NAMES utf8mb4;
USE planta;

INSERT INTO clientes (nombre, ciudad, tipo) VALUES
('Hospital Central Universitario', 'Chihuahua', 'hospital'),
('Clínica San Rafael', 'Chihuahua', 'clinica'),
('MedSupply del Norte', 'Ciudad Juárez', 'distribuidor'),
('Hospital Ángeles Delicias', 'Delicias', 'hospital'),
('Distribuidora Biomédica MX', 'Monterrey', 'distribuidor');

INSERT INTO productos (sku, nombre, categoria, precio_unitario, stock, stock_minimo) VALUES
('MON-100', 'Monitor de signos vitales MV-100', 'Monitoreo', 18500.00, 42, 20),
('OXI-020', 'Oxímetro de pulso portátil OX-20', 'Monitoreo', 1250.00, 15, 50),
('ECG-300', 'Electrocardiógrafo 12 derivaciones', 'Diagnóstico', 45200.00, 8, 5),
('BOM-050', 'Bomba de infusión volumétrica BI-50', 'Terapia', 27800.00, 3, 10),
('DES-200', 'Desfibrilador externo automático DEA-200', 'Emergencia', 39900.00, 12, 6),
('CAB-010', 'Cable de paciente ECG 10 puntas', 'Accesorios', 950.00, 210, 100);

INSERT INTO ordenes_produccion (producto_id, cantidad, estado, fecha_inicio, fecha_fin) VALUES
(1, 30, 'terminada',  '2026-08-04', '2026-08-15'),
(2, 200,'en_proceso', '2026-09-22', NULL),
(3, 10, 'terminada',  '2026-08-18', '2026-09-02'),
(4, 25, 'retenida',   '2026-09-08', NULL),
(5, 15, 'planeada',   '2026-10-12', NULL),
(6, 500,'terminada',  '2026-09-01', '2026-09-05'),
(4, 20, 'en_proceso', '2026-09-29', NULL);

INSERT INTO pedidos (cliente_id, producto_id, cantidad, fecha, estado) VALUES
(1, 1, 10, '2026-08-20', 'entregado'),
(1, 4, 8,  '2026-09-15', 'abierto'),
(2, 2, 40, '2026-09-10', 'abierto'),
(3, 6, 150,'2026-09-06', 'enviado'),
(3, 1, 12, '2026-09-25', 'abierto'),
(4, 5, 4,  '2026-09-03', 'entregado'),
(5, 3, 6,  '2026-09-12', 'enviado'),
(5, 2, 100,'2026-10-01', 'abierto');

INSERT INTO inspecciones_calidad (orden_id, fecha, resultado, defecto) VALUES
(1, '2026-08-15', 'aprobada', NULL),
(3, '2026-09-02', 'aprobada', NULL),
(4, '2026-09-19', 'rechazada', 'Lectura de flujo fuera de tolerancia (+6%) en 7 de 25 unidades'),
(6, '2026-09-05', 'aprobada', NULL),
(6, '2026-09-05', 'rechazada', 'Conector con soldadura fría en 12 cables, retrabajados');
