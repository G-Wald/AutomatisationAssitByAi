"""Bot d'automatisation basé uniquement sur la lecture de l'écran et la souris / le clavier."""

from bot.capture.window import enable_dpi_awareness

# Avant tout appel à l'API fenêtres : sans ça, la mise à l'échelle Windows (écran 4K)
# fausse les coordonnées de capture et de clic.
enable_dpi_awareness()
