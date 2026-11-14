"use strict";
// Plain-language breakdown of a shell command, in several languages.
// Deterministic and offline: the command is split into parts and every part is
// looked up in a fixed dictionary. Nothing is generated, so nothing is made up;
// parts the dictionary does not know are shown as "not in the dictionary".

const LANGS = [
  ["en", "English"], ["de", "Deutsch"], ["es", "Español"], ["fr", "Français"],
  ["it", "Italiano"], ["pt", "Português"], ["tr", "Türkçe"], ["bs", "Bosanski"],
];
const L = LANGS.map(([code]) => code);

// key: [en, de, es, fr, it, pt, tr, bs]. {x} is replaced by the part itself.
const T = {
  // ui
  title: ["What this command does", "Was dieser Befehl macht", "Qué hace este comando", "Ce que fait cette commande", "Cosa fa questo comando", "O que este comando faz", "Bu komut ne yapar", "Šta ova komanda radi"],
  steps: ["Step by step", "Schritt für Schritt", "Paso a paso", "Étape par étape", "Passo dopo passo", "Passo a passo", "Adım adım", "Korak po korak"],
  asroot: ["As administrator (root):", "Als Administrator (root):", "Como administrador (root):", "En tant qu'administrateur (root) :", "Come amministratore (root):", "Como administrador (root):", "Yönetici (root) olarak:", "Kao administrator (root):"],
  note: ["Built from a fixed dictionary, offline. The risk numbers come from the local model.", "Aus einem festen Wörterbuch, offline. Die Risiko-Zahlen kommen vom lokalen Modell.", "Generado con un diccionario fijo, sin conexión. Las cifras de riesgo vienen del modelo local.", "Issu d'un dictionnaire fixe, hors ligne. Les chiffres de risque viennent du modèle local.", "Da un dizionario fisso, offline. I numeri di rischio vengono dal modello locale.", "Feito a partir de um dicionário fixo, offline. Os números de risco vêm do modelo local.", "Sabit bir sözlükten, çevrimdışı. Risk sayıları yerel modelden gelir.", "Iz fiksnog rječnika, offline. Brojevi rizika dolaze iz lokalnog modela."],
  // generic parts
  unknownProg: ["program \"{x}\" (not in the dictionary)", "Programm „{x}“ (nicht im Wörterbuch)", "programa \"{x}\" (no está en el diccionario)", "programme « {x} » (absent du dictionnaire)", "programma \"{x}\" (non nel dizionario)", "programa \"{x}\" (não está no dicionário)", "\"{x}\" programı (sözlükte yok)", "program \"{x}\" (nije u rječniku)"],
  flag: ["option {x}", "Option {x}", "opción {x}", "option {x}", "opzione {x}", "opção {x}", "{x} seçeneği", "opcija {x}"],
  target: ["acts on: {x}", "betrifft: {x}", "afecta a: {x}", "agit sur : {x}", "agisce su: {x}", "afeta: {x}", "etkilenen: {x}", "odnosi se na: {x}"],
  // targets
  "t:/": ["the whole system (root folder /)", "das ganze System (Stammordner /)", "todo el sistema (carpeta raíz /)", "tout le système (dossier racine /)", "l'intero sistema (cartella radice /)", "o sistema inteiro (pasta raiz /)", "tüm sistem (kök klasör /)", "cijeli sistem (korijenski folder /)"],
  "t:~": ["your home folder with all your files", "deinen Benutzerordner mit all deinen Dateien", "tu carpeta personal con todos tus archivos", "ton dossier personnel avec tous tes fichiers", "la tua cartella home con tutti i tuoi file", "a sua pasta pessoal com todos os arquivos", "tüm dosyalarınla ana klasörün", "tvoj lični folder sa svim fajlovima"],
  "t:*": ["every file in this folder", "jede Datei in diesem Ordner", "todos los archivos de esta carpeta", "tous les fichiers de ce dossier", "tutti i file in questa cartella", "todos os arquivos desta pasta", "bu klasördeki tüm dosyalar", "svi fajlovi u ovom folderu"],
  "t:dev": ["a whole disk ({x})", "eine ganze Festplatte ({x})", "un disco entero ({x})", "un disque entier ({x})", "un intero disco ({x})", "um disco inteiro ({x})", "bütün bir disk ({x})", "cijeli disk ({x})"],
  // operators
  "op:&&": ["then, only if the step before worked", "danach, nur wenn der Schritt davor geklappt hat", "después, solo si el paso anterior funcionó", "ensuite, seulement si l'étape d'avant a réussi", "poi, solo se il passo prima è riuscito", "depois, só se o passo anterior funcionou", "sonra, yalnızca önceki adım başarılıysa", "zatim, samo ako je prethodni korak uspio"],
  "op:||": ["otherwise, if the step before failed", "sonst, falls der Schritt davor fehlschlägt", "si no, cuando el paso anterior falla", "sinon, si l'étape d'avant échoue", "altrimenti, se il passo prima fallisce", "senão, se o passo anterior falhar", "aksi halde, önceki adım başarısız olursa", "inače, ako prethodni korak ne uspije"],
  "op:;": ["then, in any case", "danach, in jedem Fall", "después, en cualquier caso", "ensuite, dans tous les cas", "poi, in ogni caso", "depois, em qualquer caso", "sonra, her durumda", "zatim, u svakom slučaju"],
  "op:|": ["hands its output to the next command", "gibt seine Ausgabe an den nächsten Befehl weiter", "pasa su salida al siguiente comando", "passe sa sortie à la commande suivante", "passa il suo output al comando successivo", "passa a saída para o próximo comando", "çıktısını sonraki komuta verir", "predaje svoj izlaz sljedećoj komandi"],
  "op:>": ["writes the output into a file (replaces its content)", "schreibt die Ausgabe in eine Datei (ersetzt den Inhalt)", "escribe la salida en un archivo (reemplaza su contenido)", "écrit la sortie dans un fichier (remplace son contenu)", "scrive l'output in un file (ne sostituisce il contenuto)", "grava a saída num arquivo (substitui o conteúdo)", "çıktıyı bir dosyaya yazar (içeriğini değiştirir)", "upisuje izlaz u fajl (zamjenjuje sadržaj)"],
  "op:>>": ["adds the output to the end of a file", "hängt die Ausgabe an eine Datei an", "añade la salida al final de un archivo", "ajoute la sortie à la fin d'un fichier", "aggiunge l'output alla fine di un file", "acrescenta a saída ao fim de um arquivo", "çıktıyı dosyanın sonuna ekler", "dodaje izlaz na kraj fajla"],
  // prefixes
  sudo: ["runs the next command as administrator (root), with full power over the system", "führt den nächsten Befehl als Administrator (root) aus, mit voller Macht über das System", "ejecuta el siguiente comando como administrador (root), con control total del sistema", "exécute la commande suivante en administrateur (root), avec tous les droits sur le système", "esegue il comando successivo come amministratore (root), con pieno controllo del sistema", "executa o próximo comando como administrador (root), com controle total do sistema", "sonraki komutu yönetici (root) olarak, sistem üzerinde tam yetkiyle çalıştırır", "pokreće sljedeću komandu kao administrator (root), s punom kontrolom nad sistemom"],
  // programs
  rm: ["deletes files (no recycle bin, they are gone)", "löscht Dateien (kein Papierkorb, sie sind weg)", "borra archivos (sin papelera, desaparecen)", "supprime des fichiers (pas de corbeille, ils disparaissent)", "elimina file (niente cestino, spariscono)", "apaga arquivos (sem lixeira, somem)", "dosyaları siler (çöp kutusu yok, kalıcı olarak gider)", "briše fajlove (bez korpe za smeće, nestaju)"],
  del: ["deletes files or folders", "löscht Dateien oder Ordner", "borra archivos o carpetas", "supprime des fichiers ou dossiers", "elimina file o cartelle", "apaga arquivos ou pastas", "dosya veya klasörleri siler", "briše fajlove ili foldere"],
  mv: ["moves or renames files", "verschiebt oder benennt Dateien um", "mueve o renombra archivos", "déplace ou renomme des fichiers", "sposta o rinomina file", "move ou renomeia arquivos", "dosyaları taşır veya yeniden adlandırır", "premješta ili preimenuje fajlove"],
  cp: ["copies files", "kopiert Dateien", "copia archivos", "copie des fichiers", "copia file", "copia arquivos", "dosyaları kopyalar", "kopira fajlove"],
  ls: ["lists the files in a folder (only reads)", "listet die Dateien in einem Ordner auf (liest nur)", "lista los archivos de una carpeta (solo lee)", "liste les fichiers d'un dossier (lecture seule)", "elenca i file di una cartella (solo lettura)", "lista os arquivos de uma pasta (só lê)", "bir klasördeki dosyaları listeler (sadece okur)", "prikazuje fajlove u folderu (samo čita)"],
  cat: ["shows what is inside a file (only reads)", "zeigt, was in einer Datei steht (liest nur)", "muestra el contenido de un archivo (solo lee)", "affiche le contenu d'un fichier (lecture seule)", "mostra il contenuto di un file (solo lettura)", "mostra o conteúdo de um arquivo (só lê)", "bir dosyanın içeriğini gösterir (sadece okur)", "prikazuje sadržaj fajla (samo čita)"],
  echo: ["prints text on the screen", "gibt Text auf dem Bildschirm aus", "muestra texto en pantalla", "affiche du texte à l'écran", "stampa del testo sullo schermo", "mostra texto na tela", "ekrana metin yazdırır", "ispisuje tekst na ekranu"],
  cd: ["changes the current folder", "wechselt den aktuellen Ordner", "cambia la carpeta actual", "change de dossier", "cambia la cartella corrente", "muda a pasta atual", "geçerli klasörü değiştirir", "mijenja trenutni folder"],
  mkdir: ["creates a folder", "legt einen Ordner an", "crea una carpeta", "crée un dossier", "crea una cartella", "cria uma pasta", "bir klasör oluşturur", "pravi folder"],
  chmod: ["changes who may read, change or run a file", "ändert, wer eine Datei lesen, ändern oder ausführen darf", "cambia quién puede leer, cambiar o ejecutar un archivo", "change qui peut lire, modifier ou exécuter un fichier", "cambia chi può leggere, modificare o eseguire un file", "muda quem pode ler, alterar ou executar um arquivo", "bir dosyayı kimin okuyabileceğini, değiştirebileceğini veya çalıştırabileceğini değiştirir", "mijenja ko smije čitati, mijenjati ili pokretati fajl"],
  chown: ["changes who owns a file", "ändert, wem eine Datei gehört", "cambia el dueño de un archivo", "change le propriétaire d'un fichier", "cambia il proprietario di un file", "muda o dono de um arquivo", "bir dosyanın sahibini değiştirir", "mijenja vlasnika fajla"],
  curl: ["downloads something from the internet", "lädt etwas aus dem Internet", "descarga algo de internet", "télécharge quelque chose depuis internet", "scarica qualcosa da internet", "baixa algo da internet", "internetten bir şey indirir", "preuzima nešto s interneta"],
  sh: ["runs the incoming text as a program (whatever it says)", "führt den ankommenden Text als Programm aus (egal, was drinsteht)", "ejecuta el texto recibido como programa (diga lo que diga)", "exécute le texte reçu comme un programme (quoi qu'il contienne)", "esegue il testo ricevuto come programma (qualunque cosa contenga)", "executa o texto recebido como programa (seja o que for)", "gelen metni program olarak çalıştırır (içinde ne yazarsa)", "pokreće pristigli tekst kao program (šta god da piše)"],
  dd: ["writes raw data straight onto a disk", "schreibt rohe Daten direkt auf eine Festplatte", "escribe datos en bruto directamente en un disco", "écrit des données brutes directement sur un disque", "scrive dati grezzi direttamente su un disco", "grava dados brutos direto num disco", "ham veriyi doğrudan bir diske yazar", "upisuje sirove podatke direktno na disk"],
  mkfs: ["formats a disk, everything on it is erased", "formatiert eine Festplatte, alles darauf wird gelöscht", "formatea un disco, se borra todo lo que contiene", "formate un disque, tout ce qu'il contient est effacé", "formatta un disco, tutto il contenuto viene cancellato", "formata um disco, tudo nele é apagado", "bir diski biçimlendirir, üzerindeki her şey silinir", "formatira disk, sve na njemu se briše"],
  kill: ["stops running programs", "beendet laufende Programme", "detiene programas en ejecución", "arrête des programmes en cours", "termina programmi in esecuzione", "encerra programas em execução", "çalışan programları durdurur", "zaustavlja pokrenute programe"],
  shutdown: ["shuts down or restarts the computer", "fährt den Computer herunter oder startet ihn neu", "apaga o reinicia el ordenador", "éteint ou redémarre l'ordinateur", "spegne o riavvia il computer", "desliga ou reinicia o computador", "bilgisayarı kapatır veya yeniden başlatır", "gasi ili ponovo pokreće računar"],
  ssh: ["logs in to another computer over the network", "meldet sich über das Netz an einem anderen Computer an", "inicia sesión en otro ordenador por la red", "se connecte à un autre ordinateur via le réseau", "accede a un altro computer tramite la rete", "entra em outro computador pela rede", "ağ üzerinden başka bir bilgisayara bağlanır", "prijavljuje se na drugi računar preko mreže"],
  scp: ["copies files to or from another computer", "kopiert Dateien zu oder von einem anderen Computer", "copia archivos a o desde otro ordenador", "copie des fichiers vers ou depuis un autre ordinateur", "copia file da o verso un altro computer", "copia arquivos de ou para outro computador", "başka bir bilgisayara veya bilgisayardan dosya kopyalar", "kopira fajlove na drugi računar ili s njega"],
  tar: ["packs files into an archive or unpacks one", "packt Dateien in ein Archiv oder entpackt eines", "empaqueta archivos o desempaqueta un archivo comprimido", "crée ou extrait une archive", "crea o estrae un archivio", "empacota ou desempacota um arquivo compactado", "dosyaları arşivler veya arşivi açar", "pakuje fajlove u arhivu ili je raspakuje"],
  find: ["searches for files", "sucht nach Dateien", "busca archivos", "cherche des fichiers", "cerca file", "procura arquivos", "dosya arar", "traži fajlove"],
  grep: ["searches for text (only reads)", "sucht nach Text (liest nur)", "busca texto (solo lee)", "cherche du texte (lecture seule)", "cerca testo (solo lettura)", "procura texto (só lê)", "metin arar (sadece okur)", "traži tekst (samo čita)"],
  run: ["runs a program or script", "startet ein Programm oder Skript", "ejecuta un programa o script", "lance un programme ou un script", "esegue un programma o uno script", "executa um programa ou script", "bir program veya betik çalıştırır", "pokreće program ili skriptu"],
  install: ["installs software packages", "installiert Software-Pakete", "instala paquetes de software", "installe des paquets logiciels", "installa pacchetti software", "instala pacotes de software", "yazılım paketleri kurar", "instalira softverske pakete"],
  uninstall: ["removes software packages", "entfernt Software-Pakete", "elimina paquetes de software", "supprime des paquets logiciels", "rimuove pacchetti software", "remove pacotes de software", "yazılım paketlerini kaldırır", "uklanja softverske pakete"],
  systemctl: ["controls system services (start, stop, restart)", "steuert Systemdienste (starten, stoppen, neu starten)", "controla servicios del sistema (iniciar, parar, reiniciar)", "contrôle les services système (démarrer, arrêter, redémarrer)", "controlla i servizi di sistema (avvio, arresto, riavvio)", "controla serviços do sistema (iniciar, parar, reiniciar)", "sistem servislerini yönetir (başlat, durdur, yeniden başlat)", "upravlja sistemskim servisima (pokreni, zaustavi, restartuj)"],
  execpolicy: ["changes which scripts Windows is allowed to run", "ändert, welche Skripte Windows ausführen darf", "cambia qué scripts puede ejecutar Windows", "change les scripts que Windows peut exécuter", "cambia quali script Windows può eseguire", "muda quais scripts o Windows pode executar", "Windows'un hangi betikleri çalıştırabileceğini değiştirir", "mijenja koje skripte Windows smije pokretati"],
  git: ["works with the project's version history (git)", "arbeitet mit dem Versionsverlauf des Projekts (git)", "trabaja con el historial de versiones del proyecto (git)", "agit sur l'historique des versions du projet (git)", "lavora con la cronologia delle versioni del progetto (git)", "trabalha com o histórico de versões do projeto (git)", "projenin sürüm geçmişiyle çalışır (git)", "radi s historijom verzija projekta (git)"],
  "git push": ["uploads your commits to the shared server", "lädt deine Commits auf den gemeinsamen Server hoch", "sube tus commits al servidor compartido", "envoie tes commits sur le serveur partagé", "carica i tuoi commit sul server condiviso", "envia seus commits para o servidor compartilhado", "commit'lerini ortak sunucuya yükler", "šalje tvoje commitove na zajednički server"],
  "git pull": ["downloads the latest changes and merges them", "holt die neuesten Änderungen und führt sie zusammen", "descarga los últimos cambios y los fusiona", "récupère les derniers changements et les fusionne", "scarica le ultime modifiche e le unisce", "baixa as últimas mudanças e as mescla", "son değişiklikleri indirir ve birleştirir", "preuzima najnovije promjene i spaja ih"],
  "git reset": ["moves the project back to an earlier state", "setzt das Projekt auf einen früheren Stand zurück", "devuelve el proyecto a un estado anterior", "ramène le projet à un état antérieur", "riporta il progetto a uno stato precedente", "volta o projeto a um estado anterior", "projeyi önceki bir duruma geri alır", "vraća projekat na raniji stanje"],
  "git clean": ["deletes files git does not track", "löscht Dateien, die git nicht verfolgt", "borra archivos que git no controla", "supprime les fichiers non suivis par git", "elimina i file non tracciati da git", "apaga arquivos que o git não acompanha", "git'in izlemediği dosyaları siler", "briše fajlove koje git ne prati"],
  "git checkout": ["switches branch or restores files", "wechselt den Branch oder stellt Dateien wieder her", "cambia de rama o restaura archivos", "change de branche ou restaure des fichiers", "cambia branch o ripristina file", "troca de branch ou restaura arquivos", "dal değiştirir veya dosyaları geri yükler", "mijenja granu ili vraća fajlove"],
  "git commit": ["saves your changes as a new commit", "speichert deine Änderungen als neuen Commit", "guarda tus cambios como un nuevo commit", "enregistre tes changements dans un nouveau commit", "salva le modifiche come nuovo commit", "salva suas mudanças como um novo commit", "değişikliklerini yeni bir commit olarak kaydeder", "sprema tvoje promjene kao novi commit"],
  "git status": ["shows what has changed (only reads)", "zeigt, was sich geändert hat (liest nur)", "muestra qué ha cambiado (solo lee)", "montre ce qui a changé (lecture seule)", "mostra cosa è cambiato (solo lettura)", "mostra o que mudou (só lê)", "nelerin değiştiğini gösterir (sadece okur)", "pokazuje šta se promijenilo (samo čita)"],
  "git clone": ["downloads a copy of a repository", "lädt eine Kopie eines Repositorys herunter", "descarga una copia de un repositorio", "télécharge une copie d'un dépôt", "scarica una copia di un repository", "baixa uma cópia de um repositório", "bir deponun kopyasını indirir", "preuzima kopiju repozitorija"],
  "git rebase": ["rewrites the order of commits in the history", "schreibt die Reihenfolge der Commits im Verlauf um", "reescribe el orden de los commits en el historial", "réécrit l'ordre des commits dans l'historique", "riscrive l'ordine dei commit nella cronologia", "reescreve a ordem dos commits no histórico", "geçmişteki commit sırasını yeniden yazar", "prepisuje redoslijed commitova u historiji"],
  docker: ["controls containers (docker)", "steuert Container (docker)", "controla contenedores (docker)", "contrôle des conteneurs (docker)", "controlla i container (docker)", "controla contêineres (docker)", "konteynerleri yönetir (docker)", "upravlja kontejnerima (docker)"],
  "docker run": ["starts a new container", "startet einen neuen Container", "inicia un contenedor nuevo", "démarre un nouveau conteneur", "avvia un nuovo container", "inicia um novo contêiner", "yeni bir konteyner başlatır", "pokreće novi kontejner"],
  "docker rm": ["removes containers", "entfernt Container", "elimina contenedores", "supprime des conteneurs", "rimuove container", "remove contêineres", "konteynerleri kaldırır", "uklanja kontejnere"],
  "docker system": ["cleans up docker data (images, containers, caches)", "räumt Docker-Daten auf (Images, Container, Caches)", "limpia datos de docker (imágenes, contenedores, cachés)", "nettoie les données docker (images, conteneurs, caches)", "pulisce i dati di docker (immagini, container, cache)", "limpa dados do docker (imagens, contêineres, caches)", "docker verilerini temizler (imajlar, konteynerler, önbellekler)", "čisti docker podatke (slike, kontejnere, keš)"],
  // flags
  "f:recursive": ["including every subfolder", "inklusive aller Unterordner", "incluidas todas las subcarpetas", "y tous les sous-dossiers", "incluse tutte le sottocartelle", "incluindo todas as subpastas", "tüm alt klasörler dahil", "uključujući sve podfoldere"],
  "f:force": ["without asking, even protected files", "ohne nachzufragen, auch geschützte Dateien", "sin preguntar, incluso archivos protegidos", "sans demander, même les fichiers protégés", "senza chiedere, anche i file protetti", "sem perguntar, até arquivos protegidos", "sormadan, korumalı dosyalar bile", "bez pitanja, čak i zaštićene fajlove"],
  "f:nopreserve": ["even allows deleting the root folder /", "erlaubt sogar, den Stammordner / zu löschen", "permite incluso borrar la carpeta raíz /", "autorise même la suppression du dossier racine /", "permette persino di eliminare la cartella radice /", "permite até apagar a pasta raiz /", "kök klasörü / silmeye bile izin verir", "dozvoljava čak i brisanje korijenskog foldera /"],
  "f:pushforce": ["overwrites the history on the server, work of others can be lost", "überschreibt den Verlauf auf dem Server, Arbeit anderer kann verloren gehen", "sobrescribe el historial del servidor, se puede perder trabajo de otros", "écrase l'historique du serveur, le travail des autres peut être perdu", "sovrascrive la cronologia sul server, il lavoro altrui può andare perso", "sobrescreve o histórico no servidor, o trabalho de outros pode se perder", "sunucudaki geçmişin üzerine yazar, başkalarının işi kaybolabilir", "prepisuje historiju na serveru, rad drugih se može izgubiti"],
  "f:hard": ["throws away all changes that are not committed", "verwirft alle Änderungen, die nicht committet sind", "descarta todos los cambios no confirmados", "jette tous les changements non commités", "scarta tutte le modifiche non committate", "descarta todas as mudanças não commitadas", "commit edilmemiş tüm değişiklikleri atar", "odbacuje sve promjene koje nisu commitovane"],
  "f:cleandir": ["also deletes folders", "löscht auch Ordner", "también borra carpetas", "supprime aussi les dossiers", "elimina anche le cartelle", "também apaga pastas", "klasörleri de siler", "briše i foldere"],
  "f:cleanx": ["also deletes ignored files (like .env and builds)", "löscht auch ignorierte Dateien (z. B. .env und Builds)", "también borra archivos ignorados (como .env y builds)", "supprime aussi les fichiers ignorés (comme .env et les builds)", "elimina anche i file ignorati (come .env e le build)", "também apaga arquivos ignorados (como .env e builds)", "yok sayılan dosyaları da siler (.env ve derlemeler gibi)", "briše i ignorisane fajlove (npr. .env i buildove)"],
  "f:777": ["everyone may read, change and run it", "jeder darf lesen, ändern und ausführen", "todos pueden leer, cambiar y ejecutar", "tout le monde peut lire, modifier et exécuter", "chiunque può leggere, modificare ed eseguire", "todos podem ler, alterar e executar", "herkes okuyabilir, değiştirebilir ve çalıştırabilir", "svi smiju čitati, mijenjati i pokretati"],
  "f:silent": ["quietly, without progress output", "leise, ohne Fortschrittsanzeige", "en silencio, sin mostrar progreso", "silencieusement, sans progression", "in silenzio, senza mostrare l'avanzamento", "em silêncio, sem mostrar progresso", "sessizce, ilerleme göstermeden", "tiho, bez prikaza napretka"],
  "f:follow": ["follows redirects to other addresses", "folgt Weiterleitungen zu anderen Adressen", "sigue redirecciones a otras direcciones", "suit les redirections vers d'autres adresses", "segue i reindirizzamenti ad altri indirizzi", "segue redirecionamentos para outros endereços", "başka adreslere yönlendirmeleri izler", "prati preusmjeravanja na druge adrese"],
  "f:delete": ["deletes everything it finds", "löscht alles, was es findet", "borra todo lo que encuentra", "supprime tout ce qu'il trouve", "elimina tutto ciò che trova", "apaga tudo o que encontra", "bulduğu her şeyi siler", "briše sve što pronađe"],
  "f:prune": ["deletes unused data", "löscht unbenutzte Daten", "borra datos sin usar", "supprime les données inutilisées", "elimina i dati inutilizzati", "apaga dados não usados", "kullanılmayan verileri siler", "briše nekorištene podatke"],
  // whole-command warnings
  "w:pipe-sh": ["Downloads code from the internet and runs it right away, without you seeing it first.", "Lädt Code aus dem Internet und führt ihn sofort aus, ohne dass du ihn vorher siehst.", "Descarga código de internet y lo ejecuta al instante, sin que lo veas antes.", "Télécharge du code depuis internet et l'exécute aussitôt, sans que tu le voies avant.", "Scarica codice da internet e lo esegue subito, senza che tu lo veda prima.", "Baixa código da internet e executa na hora, sem você ver antes.", "İnternetten kod indirir ve önce görmeden hemen çalıştırır.", "Preuzima kod s interneta i odmah ga pokreće, a da ga prije ne vidiš."],
};

Object.assign(T, require("./explain-more"));
T["h:del"] = T["h:shred"] = T["h:rm"];
T["h:mkfs"] = T["h:t:dev"] = T["h:dd"];
T["h:docker system"] = T["h:f:prune"];
T["docker exec"] = T["kubectl exec"];
T["kubectl describe"] = T["kubectl logs"] = T["kubectl get"];

const PROGS = {
  rm: "rm", rmdir: "del", rd: "del", del: "del", erase: "del", "remove-item": "del", ri: "del",
  mv: "mv", move: "mv", "move-item": "mv", ren: "mv", cp: "cp", copy: "cp", "copy-item": "cp",
  ls: "ls", dir: "ls", "get-childitem": "ls", gci: "ls", cat: "cat", type: "cat", "get-content": "cat", gc: "cat", less: "cat", head: "cat", tail: "cat",
  echo: "echo", "write-output": "echo", "write-host": "echo", printf: "echo", cd: "cd", "set-location": "cd", mkdir: "mkdir", md: "mkdir",
  chmod: "chmod", icacls: "chmod", chown: "chown", takeown: "chown",
  curl: "curl", wget: "curl", "invoke-webrequest": "curl", iwr: "curl", "invoke-restmethod": "curl", irm: "curl",
  sh: "sh", bash: "sh", zsh: "sh", iex: "sh", "invoke-expression": "sh", eval: "sh", source: "source", ".": "source",
  dd: "dd", mkfs: "mkfs", wipefs: "mkfs", format: "mkfs", diskpart: "mkfs", "format-volume": "mkfs", "clear-disk": "mkfs",
  kill: "kill", pkill: "kill", killall: "kill", taskkill: "kill", "stop-process": "kill",
  shutdown: "shutdown", reboot: "shutdown", poweroff: "shutdown", "restart-computer": "shutdown", "stop-computer": "shutdown",
  ssh: "ssh", scp: "scp", rsync: "scp", tar: "tar", unzip: "tar", zip: "tar", find: "find", grep: "grep", rg: "grep", "select-string": "grep",
  python: "run", python3: "run", py: "run", node: "run", java: "run", powershell: "run", pwsh: "run", cmd: "run",
  systemctl: "systemctl", service: "systemctl", "stop-service": "systemctl", "restart-service": "systemctl", "set-service": "systemctl", sc: "systemctl",
  "set-executionpolicy": "execpolicy", git: "git", docker: "docker",
  "get-process": "ps", ps: "ps", tasklist: "ps", top: "ps", htop: "ps",
  "new-item": "touch", ni: "touch", touch: "touch",
  "set-content": "write", "add-content": "write", "out-file": "write", tee: "write",
  "start-process": "start", start: "start", saps: "start",
  reg: "registry", regedit: "registry", "set-itemproperty": "registry", "new-itemproperty": "registry", "remove-itemproperty": "registry",
  netsh: "firewall", iptables: "firewall", ufw: "firewall", "new-netfirewallrule": "firewall", "set-netfirewallprofile": "firewall",
  net: "users", useradd: "users", userdel: "users", usermod: "users", passwd: "users", "new-localuser": "users", "add-localgroupmember": "users",
  schtasks: "schtask", crontab: "schtask", "register-scheduledtask": "schtask", at: "schtask",
  bcdedit: "boot", "grub-install": "boot", efibootmgr: "boot", vssadmin: "shadow", wbadmin: "shadow",
  npx: "npx", bunx: "npx", pnpx: "npx", kubectl: "kubectl", terraform: "terraform", tofu: "terraform",
  psql: "db", mysql: "db", sqlite3: "db", mongosh: "db", mongo: "db", "redis-cli": "db", sqlcmd: "db",
  nc: "nc", ncat: "nc", netcat: "nc", socat: "nc", base64: "b64", sed: "sed", shred: "shred",
};
const MANAGERS = ["npm", "pnpm", "yarn", "bun", "pip", "pip3", "apt", "apt-get", "brew", "choco", "winget", "cargo", "gem"];
const INSTALL = ["install", "i", "add", "ci"];
const UNINSTALL = ["uninstall", "remove", "rm", "purge"];
const PKGRUN = ["run", "start", "test", "exec", "x", "dlx"];
const PREFIX = { sudo: "sudo", doas: "sudo", runas: "sudo" };
const SKIP = new Set(["env", "nohup", "time", "exec", "command"]);
const SUBCMD = new Set(["git", "docker", "kubectl", "terraform"]);
const RISKY_PROGS = new Set(["rm", "del", "dd", "mkfs", "sh", "shutdown", "execpolicy", "registry", "boot", "shadow", "firewall", "users", "nc", "shred"]);
const RISKY_SUBS = new Set(["push", "reset", "clean", "rebase", "system", "delete", "destroy", "publish", "prune"]);
const RISKY_FLAGS = new Set(["f:recursive", "f:force", "f:nopreserve", "f:pushforce", "f:hard", "f:cleandir", "f:cleanx", "f:777", "f:delete", "f:prune", "f:cronr"]);
const DESTRUCTIVE = new Set(["rm", "del", "mv", "cp", "chmod", "chown", "dd", "mkfs", "shred"]);
const SQL = /\b(drop\s+(table|database|schema)|truncate(\s+table)?\s+\w|delete\s+from|flushall|flushdb)\b/i;
const DYN = /\$\(|`|\$\{|\$[A-Za-z_]|%[A-Za-z_]+%/;
const FETCH_RUN = /\b(iwr|irm|invoke-webrequest|invoke-restmethod|downloadstring|curl|wget)\b/i;
const HIDDEN = /frombase64string|\bbase64\s+(-d|--decode)\b/i;

function flagKey(prog, sub, flag) {
  const f = flag.toLowerCase();
  if (f === "-whatif" || f === "--dry-run" || f.startsWith("--dry-run=") || (prog === "git" && sub === "clean" && f === "-n")) return "f:whatif";
  if (f === "-confirm:$false" || f === "-y" || f === "--yes" || f === "--assume-yes" || f === "-auto-approve" || f === "--auto-approve") return "f:noconfirm";
  if (prog === "del" && f === "/s") return "f:recursive";
  if (prog === "del" && f === "/q") return "f:noconfirm";
  if (prog === "del" && f === "/f") return "f:force";
  if (prog === "rm" || prog === "del" || prog === "cp" || prog === "chmod" || prog === "chown") {
    if (f === "-r" || f === "--recursive" || f === "-recurse") return "f:recursive";
    if (f === "-f" || f === "--force" || f === "-force") return "f:force";
    if (f === "--no-preserve-root") return "f:nopreserve";
  }
  if (prog === "git" && sub === "push" && (f === "-f" || f === "--force")) return "f:pushforce";
  if (prog === "git" && sub === "reset" && f === "--hard") return "f:hard";
  if (prog === "git" && sub === "clean") {
    if (f === "-f" || f === "--force") return "f:force";
    if (f === "-d") return "f:cleandir";
    if (f === "-x") return "f:cleanx";
  }
  if (prog === "curl") { if (f === "-s" || f === "--silent") return "f:silent"; if (f === "-l" || f === "--location") return "f:follow"; }
  if (prog === "find" && f === "-delete") return "f:delete";
  if (prog === "docker" && (f === "-a" || f === "--all" || f === "-f" || f === "--force")) return "f:force";
  if (prog === "kubectl" && (f === "--all" || f === "--force")) return "f:force";
  if (prog === "kill" && (f === "-9" || f === "/f" || f === "-force")) return "f:force";
  if (prog === "sed" && (f === "-i" || f.startsWith("-i.") || f === "--in-place")) return "f:inplace";
  if (prog === "schtask" && f === "-r") return "f:cronr";
  return null;
}

// Shell-ish split with positions: words are {text,start,end}, operators {op,start,end}.
// Quotes keep spaces, $( … ) and `…` stay one word so runtime parts are seen as a whole.
function tokenize(cmd) {
  const out = [];
  let cur = "", q = null, start = -1, depth = 0;
  const push = (end) => { if (start >= 0) out.push({ text: cur, start, end }); cur = ""; start = -1; };
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i], two = cmd.slice(i, i + 2);
    if (q) { if (c === q) { q = null; if (c === "`") cur += c; } else cur += c; continue; }
    if (depth) { cur += c; if (c === "(") depth++; else if (c === ")") depth--; continue; }
    if (c === "'" || c === '"' || c === "`") { if (start < 0) start = i; q = c; if (c === "`") cur += c; continue; }
    if (two === "$(") { if (start < 0) start = i; cur += two; depth = 1; i++; continue; }
    if (/\s/.test(c)) { push(i); continue; }
    if (two === "&&" || two === "||" || two === ">>") { push(i); out.push({ op: two, start: i, end: i + 2 }); i++; continue; }
    if (c === "|" || c === ";" || c === ">") { push(i); out.push({ op: c, start: i, end: i + 1 }); continue; }
    if (start < 0) start = i;
    cur += c;
  }
  push(cmd.length);
  return out;
}

function t(key, lang, x) {
  const row = T[key];
  if (!row) return x || key;
  const s = row[Math.max(0, L.indexOf(lang))] || row[0];
  return x != null ? s.replace("{x}", x) : s;
}

function targetKey(tok) {
  if (tok === "/" || tok === "/*") return "t:/";
  if (/^(~|\$HOME|%USERPROFILE%|\$env:USERPROFILE)\/?$/i.test(tok)) return "t:~";
  if (tok === "*" || tok === "./*" || tok === ".\\*") return "t:*";
  if (/^\/dev\/(sd|nvme|hd|disk|mmcblk)/.test(tok) || /^of=\/dev\//.test(tok)) return "t:dev";
  return null;
}

function explain(command, lang = "en") {
  if (!L.includes(lang)) lang = "en";
  const cmdText = String(command);
  const toks = tokenize(cmdText);
  const parts = [];
  const sentences = [];
  const warnings = [];
  const safer = new Set();
  const warn = (key, x) => { const w = t(key, lang, x); if (!warnings.includes(w)) warnings.push(w); };
  let seg = null;
  let prevProg = null;
  let pipeSh = false;

  // one row per word; a known "h:<key>" adds what can go wrong and marks it risky
  const add = (tok, kind, meaning, key, risky) => {
    const p = { text: tok.text != null ? tok.text : tok.op, start: tok.start, end: tok.end, kind, meaning };
    const harm = key && T["h:" + key] ? t("h:" + key, lang) : null;
    if (harm) p.harm = harm;
    if (risky || harm) p.risky = true;
    parts.push(p);
    return p;
  };
  const close = () => {
    if (!seg) return;
    if (!seg.whatif) (seg.safer || []).forEach((k) => safer.add(k));
    if (!seg.main) return;
    let s = (seg.root ? t("asroot", lang) + " " : "") + seg.main;
    if (seg.flags.length) s += " (" + seg.flags.join(", ") + ")";
    if (seg.targets.length) s += " → " + seg.targets.join(", ");
    sentences.push(s);
  };

  for (const tok of toks) {
    if (tok.op) {
      close();
      add(tok, "op", t("op:" + tok.op, lang));
      seg = tok.op === "|" ? { pipeFrom: prevProg } : tok.op === ">" || tok.op === ">>" ? { redirect: true } : null;
      continue;
    }
    const word = tok.text;
    if (!seg || (!seg.prog && !seg.redirect)) {
      const from = seg && seg.pipeFrom;
      seg = seg || {};
      seg.flags = seg.flags || []; seg.targets = seg.targets || []; seg.safer = seg.safer || [];
      const base = word.replace(/^.*[\\/]/, "").replace(/\.exe$/i, "").toLowerCase();
      if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(word)) { add(tok, "arg", t("target", lang, word)); continue; }
      if (PREFIX[base]) { seg.root = true; seg.safer.push("s:sudo"); add(tok, "prefix", t("sudo", lang), "sudo", true); continue; }
      if (SKIP.has(base)) { add(tok, "prefix", t("run", lang)); continue; }
      seg.base = base;
      if (MANAGERS.includes(base)) { seg.prog = "pkg"; seg.main = null; add(tok, "prog", t("pkg", lang)); prevProg = "pkg"; continue; }
      if (base === "docker-compose") {
        seg.prog = "docker"; seg.sub = "compose"; seg.main = t("docker compose", lang);
        add(tok, "prog", seg.main); prevProg = "docker"; continue;
      }
      const key = PROGS[base] || (/^mkfs/.test(base) ? "mkfs" : null);
      seg.prog = key || base;
      seg.main = key ? t(key, lang) : t("unknownProg", lang, word);
      add(tok, "prog", seg.main, key, RISKY_PROGS.has(key));
      if (key === "rm") seg.safer.push("s:rm");
      if (key === "del") seg.safer.push("s:del");
      if (key === "dd" || key === "mkfs") seg.safer.push("s:dd");
      if (key === "sh" && from === "curl") pipeSh = true;
      if (key === "sh" && (from === "b64" || from === "echo")) warn("w:hidden");
      prevProg = seg.prog;
      continue;
    }
    if (seg.redirect) { add(tok, "arg", t("target", lang, word)); continue; }

    // subcommands: git push, kubectl delete, terraform destroy, npm install …
    if (!seg.sub && SUBCMD.has(seg.prog) && !word.startsWith("-")) {
      seg.sub = word.toLowerCase();
      const k = `${seg.prog} ${seg.sub}`;
      if (T[k]) { seg.main = t(k, lang); add(tok, "sub", seg.main, k, RISKY_SUBS.has(seg.sub)); }
      else add(tok, "sub", t("flag", lang, word));
      if (seg.prog === "docker" && seg.sub === "system") { seg.flags.push(t("f:prune", lang)); seg.safer.push("s:prune"); }
      if (k === "git clean") seg.safer.push("s:clean");
      if (k === "terraform destroy" || k === "terraform apply") seg.safer.push("s:tf");
      if (k === "kubectl delete" || k === "kubectl apply") seg.safer.push("s:kube");
      continue;
    }
    if (!seg.sub && seg.prog === "pkg") {
      seg.sub = word.toLowerCase();
      const k = INSTALL.includes(seg.sub) ? "install" : UNINSTALL.includes(seg.sub) ? "uninstall"
        : PKGRUN.includes(seg.sub) ? "pkgrun" : seg.sub === "publish" ? "publish" : null;
      seg.main = k ? t(k, lang) : t("pkg", lang);
      add(tok, "sub", k ? seg.main : t("flag", lang, word), k, k === "publish");
      continue;
    }
    if (seg.prog === "docker" && word === "prune") {
      const m = t("f:prune", lang); seg.flags.push(m); seg.safer.push("s:prune"); add(tok, "flag", m, "f:prune", true); continue;
    }
    if (seg.prog === "chmod" && /^(0?777|a\+rwx|ugo\+rwx)$/.test(word)) {
      const m = t("f:777", lang); seg.flags.push(m); seg.safer.push("s:777"); add(tok, "flag", m, "f:777", true); continue;
    }
    if (/^(-|\/[a-z]$)/i.test(word) && !(seg.prog === "kill" && /^\d/.test(word))) {
      if (seg.prog === "run" && /^(powershell|pwsh)$/.test(seg.base) && /^-(e|ec|enc|encodedcommand)$/i.test(word)) {
        warn("w:hidden"); add(tok, "flag", t("w:hidden", lang), null, true); continue;
      }
      if (seg.prog === "rm" && /^-[a-z]*i/i.test(word) && !word.startsWith("--")) seg.whatif = true; // rm -i asks first
      // -rf -> -r -f, but only short unknown bundles (PowerShell -Filter stays one word)
      const whole = flagKey(seg.prog, seg.sub, word);
      const flags = !whole && /^-[a-zA-Z]{2,3}$/.test(word) && seg.prog !== "find" ? word.slice(1).split("").map((c) => "-" + c) : [word];
      const keys = flags.map((f) => flagKey(seg.prog, seg.sub, f)).filter(Boolean);
      if (keys.includes("f:whatif")) seg.whatif = true;
      if (keys.includes("f:pushforce")) seg.safer.push("s:pushforce");
      if (keys.includes("f:hard")) seg.safer.push("s:hard");
      const meanings = keys.map((k) => t(k, lang));
      meanings.forEach((m) => { if (!seg.flags.includes(m)) seg.flags.push(m); });
      const harmKey = keys.find((k) => T["h:" + k]);
      add(tok, "flag", meanings.length ? meanings.join(", ") : t("flag", lang, word), harmKey, keys.some((k) => RISKY_FLAGS.has(k)) && seg.prog !== "curl");
      continue;
    }
    // arguments
    if (seg.prog === "db" && SQL.test(word)) {
      const m = t("t:sql", lang, word); seg.targets.push(word); seg.safer.push("s:sql"); add(tok, "arg", m, "t:sql", true); continue;
    }
    if (seg.prog === "sh" && FETCH_RUN.test(word)) pipeSh = true;
    if (HIDDEN.test(word)) warn("w:hidden");
    const destructive = DESTRUCTIVE.has(seg.prog);
    const tk = targetKey(word);
    if (tk) {
      const m = t(tk, lang, word.replace(/^of=/, ""));
      seg.targets.push(m);
      add(tok, "arg", m, destructive ? tk : null, destructive && tk !== "t:*");
      continue;
    }
    if (DYN.test(word)) {
      seg.targets.push(word);
      if (destructive) warn("w:dynamic", word);
      add(tok, "arg", t("t:dyn", lang, word), null, destructive);
      continue;
    }
    seg.targets.push(word);
    add(tok, "arg", t("target", lang, word));
  }
  close();
  // PowerShell style: iex (iwr …) or iwr … | iex
  if (!pipeSh && /\b(iex|invoke-expression)\b/i.test(cmdText) && FETCH_RUN.test(cmdText)) pipeSh = true;
  if (pipeSh) { warn("w:pipe-sh"); safer.add("s:pipe-sh"); }
  return {
    lang, langs: LANGS, title: t("title", lang), steps: t("steps", lang), note: t("note", lang),
    hint: t("hint", lang), harmLabel: t("harm", lang), saferTitle: t("safer", lang),
    summary: sentences, warnings, parts, safer: [...safer].map((k) => t(k, lang)),
  };
}

module.exports = { explain, LANGS, tokenize, phrase: t };
