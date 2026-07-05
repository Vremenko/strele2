<?php
// Varnostna kopija, če strežnik ne servira index.html
header('Location: index.html', true, 302);
exit;
