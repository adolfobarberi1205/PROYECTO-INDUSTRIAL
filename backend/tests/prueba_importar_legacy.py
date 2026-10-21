from app.core.legacy_adapter import cargar_motor_legacy


motor = cargar_motor_legacy()

print("Motor actual importado correctamente.")
print(
    "Función cargar_xml_repositorio:",
    callable(motor.cargar_xml_repositorio),
)
print(
    "Función reconstruir_proceso:",
    callable(motor.reconstruir_proceso),
)
print("La interfaz Tkinter no fue abierta.")