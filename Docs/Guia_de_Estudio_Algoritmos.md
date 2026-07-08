# Guía de Estudio — Los Algoritmos de la Marca de Agua Robusta

**Una explicación paso a paso, pensada para entenderse sin ser experto.**
Teoría de la Información · Universidad Nacional de Colombia · 2026

---

## Cómo usar esta guía

Esta guía acompaña la presentación y el documento teórico del proyecto **RSW** (*Robust
Spread‑spectrum Watermark*). Aquí no
damos por sentado casi nada: cada algoritmo se explica primero con una **idea intuitiva** (a
menudo con una analogía cotidiana), luego con su **fórmula** (contada con calma) y por último
con **cómo y por qué lo usamos**. Si lees de principio a fin, deberías poder explicarle el
sistema a un compañero que nunca haya oído hablar de esteganografía.

Para cada tema encontrarás:

- 🎯 **La idea** — una frase y una analogía.
- 📐 **La matemática** — la fórmula, explicada término a término.
- 🔧 **En el proyecto** — dónde y con qué valores se usa de verdad.
- 💡 **Por qué importa** — qué ganamos con ello.

> **Una nota sobre el nombre.** El proyecto se llama **RSW** (*Robust Spread‑spectrum
> Watermark*) porque eso es justo lo que construimos: **una marca de agua robusta** basada en
> espectro ensanchado (ISS), con Brotli como compresor. Su *punto de partida* teórico fueron dos
> técnicas llamadas *ANS* y *STC*: les dedicamos una sección de *contexto*, porque explican de
> dónde viene la idea rectora, pero **no forman parte del sistema final**.

---

# Parte 0 · El problema, en cristiano

## ¿Qué estamos resolviendo?

Hoy cualquiera puede fabricar imágenes falsas con Inteligencia Artificial (los famosos
*deepfakes*). Por eso ya no basta con «ver para creer». Nos gustaría poder **firmar** una
imagen auténtica, de forma **invisible**, para que después alguien pueda **comprobar** que la
firma sigue ahí y que la imagen viene de quien dice venir.

A esa firma invisible la llamamos **marca de agua de procedencia**. La dificultad no es
ponerla —eso es fácil—, sino que **sobreviva**. Cuando subes una foto a Facebook, WhatsApp o
Pinterest, la plataforma la **comprime** (para que pese menos) y, sobre todo, la **reduce de
tamaño** (la reescala). Ambas cosas suelen borrar cualquier marca escondida.

### Dos palabras que conviene distinguir

- **Esteganografía**: esconder un mensaje *secreto* dentro de otra cosa, de modo que nadie note
  que hay un mensaje. Piensa en tinta invisible.
- **Marca de agua (watermarking)**: incrustar una señal que identifica o autentica el medio.
  No pretende ser secreta; pretende ser **robusta** y **difícil de falsificar**. Piensa en el
  sello de agua de un billete: se ve al trasluz, y está ahí precisamente para que confíes en él.

Nuestro sistema es del segundo tipo: **una marca de agua robusta**.

## El problema, dicho como ingeniero

Llamemos $\mathbf{x}$ a la imagen original (el «portador»), $\mathbf{m}$ al mensaje que
queremos incrustar, y $C(\cdot)$ al «canal»: todo lo que la red social le hace a la imagen
(reescalar y luego recomprimir). Queremos un codificador $E$ (que pone la marca) y un
decodificador $D$ (que la lee) tales que

$$ D\big(C(E(\mathbf{x},\mathbf{m}))\big) = \mathbf{m}. $$

En palabras: **después** de que la imagen marcada pase por la red social, todavía podemos
recuperar el mensaje intacto. Y todo esto **sin que la marca se note** a simple vista.

> 💡 **La tensión central.** Robustez e imperceptibilidad tiran en direcciones opuestas: cuanto
> más fuerte metes la marca (más robusta), más se nota (menos invisible). Casi todo el diseño
> consiste en ganar en ambas a la vez, y el truco es siempre el mismo: **cambiar la imagen lo
> menos posible**.

---

# Parte 1 · El sistema de un vistazo

La marca de agua se construye encadenando cuatro ideas. Esta es la tubería completa:

```
  texto  ──Brotli──▶  comprimido  ──Reed-Solomon──▶  protegido
                                                          │
                                                          ▼
                             ISS (espectro ensanchado) sobre la
                             DCT global de una malla canónica
                                                          │
                                                          ▼
                                                   imagen marcada (PNG)
```

Leído de izquierda a derecha:

1. **Brotli** comprime el texto → tenemos *menos bits* que esconder.
2. **Reed‑Solomon** añade «paridad» → podremos *reparar* errores.
3. **ISS** reparte cada bit sobre muchos coeficientes de la imagen, en una malla de tamaño
   fijo → el reescalado ya *no borra* la marca.

Las siguientes partes explican cada pieza. Antes, un mapa de qué idea resuelve qué:

| Reto | Herramienta | Parte |
|---|---|---|
| Tener pocos bits que esconder | Compresión (Brotli) | 2 |
| Sobrevivir al reescalado | Malla canónica + ISS | 5 |
| Reparar los errores del canal | Reed‑Solomon | 5 |
| No devolver basura si no hay marca | CRC‑16 | 5 |
| No perder la marca en fotos enormes | Cota de Nyquist | 5 |
| Que no se note | Máscara perceptual | 5 |

---

# Parte 2 · Codificación de fuente: por qué comprimimos primero

## 2.1 Entropía de Shannon (el «suelo» de bits)

🎯 **La idea.** La *entropía* mide cuánta información hay de verdad en un mensaje, es decir,
cuántos bits como mínimo hacen falta para escribirlo sin perder nada. 

*Analogía:* es como preguntar cuánto espacio ocupa tu ropa **bien doblada** en la maleta. Por
mucho que la reorganices, hay un mínimo que no puedes bajar.

📐 **La matemática.** Para una fuente $X$ cuyos símbolos aparecen con probabilidad $p(x)$:

$$ H(X) = -\sum_{x} p(x)\,\log_2 p(x) \quad \text{(bits por símbolo).} $$

Cada término $-\log_2 p(x)$ es la «sorpresa» de ver el símbolo $x$: si es muy probable, casi no
sorprende (pocos bits); si es raro, sorprende mucho (muchos bits). La entropía es el promedio
de esa sorpresa. El **teorema de codificación de fuente** de Shannon (1948) dice que **nadie
puede comprimir por debajo de $H(X)$** sin perder información, y que ese límite es alcanzable.

🔧 **En el proyecto.** Es la razón de comprimir el texto *antes* de incrustarlo.

💡 **Por qué importa.** En una marca de agua, **cada bit que metemos es una modificación de la
imagen**. Menos bits ⇒ podemos repartir cada bit sobre *más* píxeles ⇒ la marca es más robusta
*y* más invisible a la vez. Comprimir no es un detalle: es lo que hace posible todo lo demás.

## 2.2 Huffman y Lempel‑Ziv (las dos grandes familias)

🎯 **La idea.** Hay dos maneras clásicas de acercarse a ese límite de Shannon:

- **Huffman (1952)**: da códigos *cortos* a los símbolos frecuentes y *largos* a los raros,
  construyendo un árbol binario. *Analogía:* como la taquigrafía, donde las palabras comunes se
  abrevian más. Es óptimo… si te obligas a usar un número entero de bits por símbolo.
- **Lempel‑Ziv / LZ77 (1977)**: en vez de mirar símbolo a símbolo, detecta **trozos que se
  repiten** y los reemplaza por una referencia «vuelve $d$ caracteres atrás y copia $\ell$».
  *Analogía:* «ídem» o «véase arriba». Captura la redundancia de frases enteras.

📐 **La matemática (LZ77).** Emite tripletas $(\text{distancia}, \text{longitud}, \text{símbolo
siguiente})$. Es *universal*: se acerca a la entropía sin saber de antemano las probabilidades.

🔧 **En el proyecto.** No usamos Huffman ni LZ por separado: usamos **Brotli**, que los combina.

## 2.3 Brotli (el compresor que sí usamos)

🎯 **La idea.** Brotli = LZ77 + Huffman + **un diccionario de palabras comunes ya incorporado**.
Ese diccionario es clave para *textos cortos*: aunque tu frase sea nueva, muchas de sus
palabras ya están «pre‑cargadas», así que se codifican con muy pocos bits.

🔧 **En el proyecto.** El texto se comprime con `brotli.compress(..., mode=MODE_TEXT)`. Por eso
el límite de **512 bytes** es sobre el tamaño **comprimido**: una frase normal en español puede
ocupar bastante más de 512 caracteres y aun así caber. Si comprimir no ayuda (texto muy corto o
aleatorio), se guarda tal cual, y una bandera lo indica.

💡 **Por qué importa.** Un párrafo de ~250 caracteres puede encogerse ~45 %. Esos bits ahorrados
se convierten en más robustez y mejor calidad de imagen.

---

# Parte 3 · Contexto: el punto de partida (ANS y STC)

> Esta sección es de **cultura general del proyecto**: explica de dónde viene la idea rectora.
> **El sistema final no usa estas técnicas** (usa Brotli e ISS), pero entenderlas ayuda a
> apreciar por qué tomamos las decisiones que tomamos.

## 3.1 ANS (Sistemas Numéricos Asimétricos)

🎯 **La idea.** Es un codificador entrópico moderno (Duda, 2014) que representa **todo el
mensaje como un único número gigante** que va creciendo símbolo a símbolo. Combina la
*velocidad* de Huffman con la *eficiencia* de la codificación aritmética.

📐 **La matemática.** Para un símbolo $s$ con frecuencia $f_s$ y frecuencia acumulada $c_s$,
sobre un total $M=2^{b}$, el estado $x$ avanza así:

$$ x' = \left\lfloor \frac{x}{f_s} \right\rfloor \cdot M + (x \bmod f_s) + c_s. $$

Es exactamente invertible, así que se puede decodificar sin pérdida. Fue la mitad del *punto de
partida* teórico del proyecto («ANS»). En la práctica, **Brotli** cumple el mismo papel de comprimir.

## 3.2 STC (Códigos de Síndrome‑Trellis) y el algoritmo de Viterbi

🎯 **La idea.** Es la otra mitad de ese punto de partida («STC»). Es una forma *óptima* de esconder muchos
bits **cambiando la imagen lo menos posible**. Dado un mensaje $\mathbf{m}$, busca la imagen
modificada $\mathbf{y}$ que cumple una ecuación lineal $\mathbf{H}\mathbf{y}=\mathbf{m}$ (lo que
hace la lectura trivial) pero con el **mínimo coste**:

$$ \mathbf{y} = \arg\!\min_{\mathbf{z}\,:\;\mathbf{H}\mathbf{z}=\mathbf{m}} \ \sum_i \rho_i\,[\,x_i \ne z_i\,]. $$

Ese mínimo se busca como el «camino más barato» en una malla (un *trellis*), y se resuelve con
el clásico **algoritmo de Viterbi** (1967) —el mismo que usan el GPS y los módems—. El coste
$\rho_i$ de cada cambio lo da una función perceptual (tipo **J‑UNIWARD**, basada en *wavelets*)
que prefiere las zonas con textura.

💡 **Por qué NO lo usamos.** El STC trabaja sobre bloques de $8\times 8$ píxeles. Ese enfoque es
excelente contra la recompresión… pero **muere cuando la imagen se reescala**, porque el
reescalado desordena la retícula de bloques. Y sobrevivir al reescalado es justo nuestro
objetivo. Así que nos quedamos con su **principio rector** —*cambiar lo menos posible*— y lo
llevamos a otra técnica que sí aguanta el cambio de tamaño: el espectro ensanchado (Parte 5).

---

# Parte 4 · El dominio de la frecuencia y el ojo humano

## 4.1 La Transformada Discreta del Coseno (DCT)

🎯 **La idea.** En vez de trabajar con píxeles, conviene ver la imagen como una **suma de
ondas** (patrones de rayas de distinta frecuencia). La DCT hace esa traducción.

*Analogía:* es como describir un acorde musical por las notas que lo componen, en lugar de por
la forma exacta de la onda de sonido. Igual de válido, pero mucho más útil para manipularlo.

📐 **La matemática.** La DCT‑II bidimensional y *ortonormal* de un bloque $f(x,y)$ de $N\times N$:

$$ F(u,v) = \alpha(u)\alpha(v)\sum_{x=0}^{N-1}\sum_{y=0}^{N-1} f(x,y)
\cos\!\Big[\tfrac{\pi(2x+1)u}{2N}\Big]\cos\!\Big[\tfrac{\pi(2y+1)v}{2N}\Big], $$

con $\alpha(0)=\sqrt{1/N}$ y $\alpha(k)=\sqrt{2/N}$ para $k>0$. La palabra clave es
*ortonormal*: significa que la energía se conserva (teorema de Parseval), así que una distorsión
medida en frecuencia equivale a la misma distorsión en píxeles. La DCT (Ahmed, Natarajan y Rao,
1974) es el corazón de JPEG y **concentra casi toda la energía en pocas frecuencias bajas**.

🔧 **En el proyecto.** Nuestra marca calcula **una única DCT global** sobre la luminancia de la
imagen (no bloques de $8\times 8$). Incrusta en una **banda de frecuencias medias** (de 8 a 400
ciclos): las bajas son demasiado visibles y las altas las destruye JPEG primero, así que el
punto medio es el equilibrio ideal.

## 4.2 El sistema visual humano (por qué escondemos en la textura)

🎯 **La idea.** El ojo **no** es igual de sensible en todas partes: perdona mucho más ruido en
zonas con textura (hierba, arena, pelo) que en zonas lisas (un cielo azul, la piel). A esto se
le llama *enmascaramiento por contraste*.

*Analogía:* susurrar en una fiesta ruidosa pasa desapercibido; susurrar en una biblioteca en
silencio, no. La textura es «la fiesta ruidosa» donde podemos escondernos.

🔧 **En el proyecto.** Los modelos perceptuales sobre la DCT (Watson, 1993) convierten esto en
números. Nuestra marca usa una **máscara** (Parte 5.3) que la concentra en la textura y la retira
de las zonas lisas.

---

# Parte 5 · El corazón: la marca de agua robusta

## 5.1 Resincronización a una resolución canónica

🎯 **La idea.** El gran enemigo es el **reescalado**, porque descoloca cualquier retícula. La
solución es astuta: **antes de hacer nada**, tanto quien pone la marca como quien la lee
estiran la imagen a un **tamaño fijo** $S\times S$ (con $S=1152$). Así, da igual a qué tamaño la
haya dejado la red social: siempre volvemos al mismo «papel cuadriculado».

*Analogía:* imagina que escondes marcas en un mapa. Si alguien fotocopia el mapa más grande o
más pequeño, tus marcas se mueven. Pero si **siempre** redibujas el mapa al tamaño de una hoja
A4 antes de buscar, las marcas vuelven a su sitio.

📐 **La matemática.**

$$ Y_c = \mathcal{R}_{S\times S}(Y), \qquad S = 1152, $$

donde $\mathcal{R}$ es el remuestreo y $Y$ la luminancia. Trabajamos sobre la DCT de $Y_c$, y
solo la *perturbación* de la marca se devuelve al tamaño original, así que la foto conserva su
aspecto.

💡 **Por qué importa.** Es **la** idea que convierte un esquema frágil en uno resistente al
reescalado. La retícula de incrustación ya nunca se pierde.

## 5.2 Espectro Ensanchado Mejorado (ISS) — el algoritmo estrella

🎯 **La idea.** En lugar de poner un bit en un solo coeficiente (frágil), lo **repartimos** sobre
decenas de coeficientes con un patrón de signos $\pm 1$ pseudoaleatorio. Al promediar todos en
la lectura, el ruido de la compresión se cancela y la señal emerge: es la «ganancia de
procesamiento».

*Analogía:* si le dices una palabra al oído a 50 personas y luego les pides que la repitan todas
a la vez, aunque algunas se equivoquen, la mayoría acierta y tú entiendes la palabra. Repartir
la información la hace resistente al ruido.

La versión **mejorada** (ISS, Malvar y Florêncio 2003) añade un truco: **cancela la propia
imagen** de la ecuación, para que no estorbe.

📐 **La matemática.** Sea $G_i$ el grupo de $L$ coeficientes del bit $b_i$, con signos $s_c$ y
amplitud $\alpha$. Definimos el objetivo $\tau_i=\alpha(2b_i-1)$ (es $+\alpha$ si el bit es 1,
$-\alpha$ si es 0) y la «proyección previa» $p_i=\tfrac{1}{\sqrt L}\sum_{c} s_c D(c)$. La regla
de inserción es:

$$ D'(c) = D(c) + \frac{\tau_i - p_i}{\sqrt{L}}\, s_c. $$

Lo bonito: como $s_c^2 = 1$, si recalculas la proyección **después**, sale exactamente $\tau_i$;
el término de la imagen $p_i$ **desaparece**. La lectura es *ciega* y solo mira el **signo**:

$$ \hat{b}_i = \mathbb{1}\!\left[\ \sum_{c\in G_i} s_c\,\tilde D(c) > 0\ \right], $$

donde $\tilde D$ es lo que llega tras el canal.

🔧 **En el proyecto.** Banda de frecuencias $[8,400)$, amplitud base $\alpha=100$, con un patrón
de signos fijado por una semilla.

💡 **Por qué importa.** Como la decisión es por signo, **el lector no necesita conocer la
amplitud** $\alpha$. Eso permite el truco de la Parte 5.4.

## 5.3 La máscara perceptual (que no se note)

🎯 **La idea.** Multiplicamos la marca por un «mapa de textura»: fuerte donde hay detalle, suave
donde la imagen es lisa. Así respetamos al ojo (Parte 4.2).

📐 **La matemática.** Con la varianza local en una ventana $w\times w$, la actividad es
$a(x,y)=\sqrt{\operatorname{Var}_w[Y_c]}$, y la máscara:

$$ \mathrm{mask}(x,y) = \operatorname{clip}\!\Big(\frac{a(x,y)}{\overline{a}},\ 0{,}72,\ 3{,}0\Big). $$

🔧 **En el proyecto.** El «suelo» $0{,}72 > 0$ es importante: garantiza que incluso un cielo liso
conserve *algo* de marca, para que sobreviva una descarga agresiva.

## 5.4 Fuerza adaptativa por carga útil

🎯 **La idea.** Un mensaje largo tiene *menos* coeficientes por bit, así que su marca es más
débil. Para compensar, subimos automáticamente la amplitud cuando el mensaje es largo.

📐 **La matemática.** La fiabilidad crece como $\sqrt{L}$ (coeficientes por bit), así que:

$$ \alpha_{\text{carga}} = \alpha \cdot \min\!\Big(\beta_{\max},\ \sqrt{\tfrac{C_{\text{ref}}}{L}}\Big), \qquad C_{\text{ref}}=400,\ \beta_{\max}=1{,}60. $$

💡 **Por qué importa.** Los mensajes cortos se insertan «en voz baja» (imagen casi perfecta,
~38 dB); los largos suben la voz lo justo (~31 dB) para mantener el mismo margen. Y como la
lectura es por signo, este ajuste es **invisible para el lector**: no hay que decírselo.

## 5.5 La cota de Nyquist (por qué limitamos el tamaño de trabajo)

🎯 **La idea.** Aquí es donde un teorema «de pizarra» resolvió un fallo real. El **teorema de
muestreo de Nyquist‑Shannon** dice que, al reducir una imagen a $W$ píxeles de ancho, se pierden
todas las frecuencias por encima de $W/2$.

*Analogía:* una cámara de pocos megapíxeles no puede capturar detalles finísimos: sencillamente
no «caben». Reducir una imagen es como bajar los megapíxeles.

📐 **La matemática.** Muestrear por debajo de $2 f_{\max}$ destruye la información: todo lo que
supere la frecuencia de Nyquist $W/2$ lo elimina el filtro anti‑*aliasing*.

🔧 **En el proyecto.** Si insertamos sobre una foto enorme, la marca tiene que atravesar un
remuestreo de gran factor (canónico → nativo → descarga) que erosiona la banda media. La
solución: **acotar el tamaño de trabajo a $S_{\max}=2048$ px** antes de insertar. Esto limita ese
factor y, de paso, coincide con lo que las plataformas guardan igualmente. Fue **la corrección
clave** para las fotos de alta resolución.

## 5.6 Reed‑Solomon, CRC y el contenedor

### Reed‑Solomon (reparar los errores que quedan)

🎯 **La idea.** Añadimos bytes «de repuesto» (paridad) para poder **reconstruir** un número
limitado de bytes dañados. *Analogía:* como mandar un paquete con relleno y una copia de la
etiqueta, por si el transporte estropea una parte.

📐 **La matemática.** Un código $\mathrm{RS}(n,k)$ sobre el cuerpo $\mathrm{GF}(2^8)$ ve $k$ bytes
como un polinomio y lo evalúa en $n=255$ puntos, añadiendo $n-k$ bytes de paridad. Corrige hasta

$$ t = \left\lfloor \frac{n-k}{2} \right\rfloor \text{ bytes por bloque.} $$

🔧 **En el proyecto.** Usamos $n-k=32$, es decir, **corrige 16 bytes por bloque**. Es ideal para
errores en «ráfaga»: un byte cuenta como *un* error aunque fallen sus 8 bits.

### CRC‑16 (no devolver basura)

🎯 **La idea.** Un CRC es una suma de verificación que *detecta* (no repara) si la cabecera está
dañada. Su trabajo es evitar **falsos positivos**.

📐 **La matemática.** Se toma el resto módulo el polinomio $g(x)=x^{16}+x^{12}+x^{5}+1$ (estándar
CCITT). Si no cuadra, no hay marca válida.

💡 **Por qué importa.** Gracias al CRC, ante una imagen sin marca el sistema responde
honestamente **«no hay marca»** en vez de inventarse un texto.

### El contenedor

La carga final es `texto → Brotli → Reed‑Solomon`, precedida de una **cabecera de 64 bits**:

```
 MAGIC (16) │ LEN (16) │ NSYM (8) │ CRC-16 (16) │ FLAGS (8)
```

La cabecera se lee **primero** y dice cuántos bits de carga hay que leer; por eso los mensajes
cortos usan menos bits (mejor calidad y más margen). Al descomprimir, se limita el tamaño
expandido para prevenir una «bomba de descompresión».

---

# Parte 6 · ¿Qué tan detectable es? (esteganálisis)

🎯 **La idea.** Robustez e indetectabilidad son opuestas. El proyecto incluye un pequeño
*banco de pruebas* para **medir** cuán fácil es que un detector note la marca.

📐 **La matemática.**

- **Características SPAM** (Pevný, Bas y Fridrich, 2010): se miran los pequeños «residuos de
  ruido» entre píxeles vecinos y se modelan como una cadena de Markov; sale un vector de 686
  números que resume la «huella estadística» de la imagen.
- **Discriminante lineal de Fisher**: un clasificador sencillo que intenta separar imágenes
  «con marca» de «sin marca».
- **Métrica $P_E$** (error del detector, con validación cruzada):

$$ P_E = \min_t\ \tfrac{1}{2}\big(P_{\mathrm{FA}}(t) + P_{\mathrm{MD}}(t)\big), \qquad
\begin{cases} P_E \approx 0{,}5 & \text{indetectable} \\ P_E \approx 0 & \text{detectable.} \end{cases} $$

🔧 **Resultado.** Nuestra marca da $P_E\approx 0$: es **totalmente detectable, a propósito**. No
es un fallo: una marca de agua **no** pretende ser secreta, sino **robusta e infalsificable**. Su
valor está en resistir el reescalado, no en esconderse.

---

# Parte 7 · Las métricas de validación

| Métrica | Qué mide | Idea rápida |
|---|---|---|
| **PSNR** | Imperceptibilidad | $10\log_{10}\dfrac{255^2}{\text{MSE}}$. Cuanto más alto, más invisible. ~38 dB (mensaje corto) → ~31 dB (carga máxima). |
| **SSIM** | Fidelidad estructural | Compara luminancia, contraste y estructura en ventanas locales (Wang et al., 2004). Más cercana a la percepción que el simple error cuadrático. |
| **BER** | Integridad | Fracción de bits del mensaje mal leídos tras el canal. Debe caer bajo la capacidad correctora de Reed‑Solomon para una recuperación exacta. |
| **Divergencia KL** | Seguridad | $D_{\mathrm{KL}}(P\Vert Q)=\sum_x P(x)\log\frac{P(x)}{Q(x)}$. La $\varepsilon$‑seguridad de Cachin (1998): a menos cambios, menor divergencia. |

---

# Resultados reales (medidos)

- **Marca de 19 bytes** («authentic:mike‑2026»): **PSNR 38,45 dB** e imagen recuperada de forma
  exacta.
- **Marca de 512 bytes**: **0 errores de bit** a través de tuberías fieles de Facebook, WhatsApp
  y Pinterest (reducción a 2048/1600/736/1024 px + JPEG de calidad 60–85), y sigue recuperándose
  incluso en descargas pequeñas del feed de Facebook hasta ~480 px.
- **94 pruebas automáticas** superadas: cubren el ciclo marcar/verificar, la robustez al
  reescalado y a JPEG, y el rechazo correcto de imágenes sin marca.
- **Límite honesto:** resiste reescalado + recompresión + rotación de 90°, pero **no** el recorte
  duro ni las capturas de pantalla (mueven la retícula de sincronía).

---

# Glosario rápido

- **Portador**: la imagen donde escondemos la marca.
- **Estego / marcada**: la imagen ya con la marca dentro.
- **Canal**: todo lo que la red social le hace a la imagen (reescalar + recomprimir).
- **DCT**: transformada que ve la imagen como suma de ondas (frecuencias).
- **Coeficiente**: cada número de la DCT; en él escondemos parte de la señal.
- **Luminancia (Y)**: el «brillo» de la imagen, separado del color.
- **Espectro ensanchado (ISS)**: repartir cada bit sobre muchos coeficientes.
- **Malla canónica**: tamaño fijo ($1152\times1152$) al que normalizamos siempre.
- **Reed‑Solomon**: código que repara bytes dañados.
- **CRC**: suma de verificación que solo detecta daños (no repara).
- **PSNR / SSIM / BER**: métricas de calidad, parecido y errores.
- **$P_E$**: error del detector; 0,5 = indetectable, 0 = detectable.

---

# Preguntas para autoevaluarte

1. Explica con tus palabras por qué **comprimir** el mensaje lo hace *más robusto*, no solo más
   pequeño.
2. ¿Qué mide la entropía de Shannon y por qué existe un límite que no se puede bajar?
3. ¿Por qué el sistema usa una **DCT global** y no bloques de $8\times 8$ como JPEG?
4. Cuenta, con la analogía de las 50 personas, cómo el espectro ensanchado resiste el ruido.
5. Demuestra (o explica) por qué, en ISS, la proyección **después** de insertar vale exactamente
   $\tau_i$ y el término de la imagen desaparece.
6. ¿Por qué el lector de la marca **no** necesita conocer la amplitud $\alpha$? ¿Qué ventaja da
   eso?
7. Usando el teorema de muestreo, explica por qué acotar el trabajo a 2048 px arregló el fallo
   de las fotos de alta resolución.
8. Un bloque Reed‑Solomon con $n-k=32$: ¿cuántos **bytes** erróneos aguanta? ¿Por qué se cuenta
   por bytes y no por bits?
9. ¿Qué significa que la marca tenga $P_E\approx 0$? ¿Por qué **no** es un problema para una
   marca de agua?
10. Explica la diferencia entre **esteganografía** y **marca de agua**, y di a cuál pertenece
    este proyecto.

---

# Referencias (formato IEEE)

Shannon 1948 · Huffman 1952 · Ziv y Lempel 1977 · Duda 2014 (ANS) · Ahmed, Natarajan y Rao 1974
(DCT) · Reed y Solomon 1960 · Viterbi 1967 · Filler, Judas y Fridrich 2011 (STC) · Holub,
Fridrich y Denemark 2014 (J‑UNIWARD) · Cox et al. 1997 y Malvar y Florêncio 2003 (espectro
ensanchado) · Nyquist 1928 / Shannon 1949 (muestreo) · Watson 1993 · Wang et al. 2004 (SSIM) ·
Pevný, Bas y Fridrich 2010 (SPAM) · Cachin 1998 ($\varepsilon$‑seguridad).

---

*Complemento de la presentación (`Presentation/index.html`) y del documento teórico
(`Docs/Fundamentos_Teoricos_RSW.tex`).*
