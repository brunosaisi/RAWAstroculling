import os
import shutil
import glob
import warnings
import rawpy
import numpy as np
from astropy.stats import sigma_clipped_stats
from astropy.convolution import Gaussian2DKernel, convolve
from photutils.detection import DAOStarFinder
from photutils.morphology import data_properties

# Importações para a Interface Gráfica Autônoma
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading

# Desativa avisos visuais do Astropy para limpar o terminal
warnings.filterwarnings("ignore")

def analisar_excentricidade_nef(caminho_nef):
    """Analisa o alongamento real de rastreio usando excentricidade ponderada pelo brilho corrigida."""
    try:
        with rawpy.imread(caminho_nef) as raw:
            # Extração linear pura de 16-bits
            imagem_crua = raw.postprocess(
                user_flip=0, 
                half_size=True, 
                no_auto_bright=True, 
                output_bps=16
            ).astype(np.float32)
            
        if len(imagem_crua.shape) == 3:
            imagem_crua = np.mean(imagem_crua, axis=2)
            
        h, w = imagem_crua.shape
        
        # Focar apenas no CENTRO do frame (Corte de 50% da área útil)
        margem_y, margem_x = int(h * 0.25), int(w * 0.25)
        centro_imagem = imagem_crua[margem_y:h-margem_y, margem_x:w-margem_x]
        hc, wc = centro_imagem.shape

        # Suavização local para controle do padrão Bayer
        kernel = Gaussian2DKernel(x_stddev=1.2)
        centro_suave = convolve(centro_imagem, kernel, boundary='extend')
        
        mean, median_bg, std = sigma_clipped_stats(centro_suave, sigma=3.0)
        if std <= 0 or np.isnan(std):
            return None
            
        # Detector sensível calibrado para perfis estelares reais
        daofind = DAOStarFinder(fwhm=4.5, threshold=3.5 * std)
        fontes = daofind(centro_suave - median_bg)
        
        if fontes is None or len(fontes) < 5:
            return None  
            
        colunas = fontes.colnames
        col_x = 'xcentroid' if 'xcentroid' in colunas else ('x_centroid' if 'x_centroid' in colunas else 'xcenter')
        col_y = 'ycentroid' if 'ycentroid' in colunas else ('y_centroid' if 'y_centroid' in colunas else 'ycenter')
        col_peak = 'peak' if 'peak' in colunas else 'peak_value'
        
        # Filtra estrelas saturadas
        fontes = fontes[fontes[col_peak] < (np.max(centro_imagem) * 0.93)]
        fontes.sort('flux')
        
        # Pega as 30 estrelas mais brilhantes do centro
        top_fontes = fontes[-30:] if len(fontes) > 30 else fontes 

        pesos_fluxo = []
        excentricidades = []

        for estrela in top_fontes:
            x, y = int(estrela[col_x]), int(estrela[col_y])
            tamanho_recorte = 12
            
            if (tamanho_recorte < x < wc - tamanho_recorte) and (tamanho_recorte < y < hc - tamanho_recorte):
                recorte = centro_imagem[y-tamanho_recorte:y+tamanho_recorte, x-tamanho_recorte:x+tamanho_recorte]
                
                if recorte.shape == (tamanho_recorte*2, tamanho_recorte*2):
                    _, med_local, _ = sigma_clipped_stats(recorte, sigma=3.0)
                    recorte_subtraido = recorte - med_local
                    
                    props = data_properties(recorte_subtraido)
                    
                    eixo_maior_obj = getattr(props, 'semimajor_axis', getattr(props, 'semimajor_sigma', None))
                    if eixo_maior_obj is not None:
                        eixo_maior_num = float(getattr(eixo_maior_obj, 'value', eixo_maior_obj))
                        
                        # Filtro morfológico estrito contra hot pixels
                        if eixo_maior_num < 1.0:
                            continue
                        
                    if props.eccentricity is not None and not np.isnan(props.eccentricity):
                        ecc_puro = float(getattr(props.eccentricity, 'value', props.eccentricity))
                        
                        # Mapeia dinamicamente o fluxo do segmento da photutils
                        fluxo_obj = getattr(props, 'segment_flux', getattr(props, 'source_sum', None))
                        
                        if fluxo_obj is not None:
                            fluxo_puro = float(getattr(fluxo_obj, 'value', fluxo_obj))
                        else:
                            fluxo_puro = 1.0
                        
                        if 0.05 < ecc_puro < 0.99 and fluxo_puro > 0:
                            excentricidades.append(ecc_puro)
                            pesos_fluxo.append(fluxo_puro)
                            
        if len(excentricidades) < 3:
            return None
            
        # Excentricidade Ponderada pelo Fluxo Luminoso Real
        excentricidade_ponderada = np.average(excentricidades, weights=pesos_fluxo)
        
        return excentricidade_ponderada
        
    except Exception as e:
        return None

def executar_triagem(diretorio_fotos, threshold_limite, widget_texto_status, janela_root, btn_trigger):
    """Executa a movimentação física dos arquivos atualizando a GUI de forma segura."""
    pasta_boas = os.path.join(diretorio_fotos, "Boas_Redondas")
    pasta_ruins = os.path.join(diretorio_fotos, "Ruins_Esticadas")
    
    os.makedirs(pasta_boas, exist_ok=True)
    os.makedirs(pasta_ruins, exist_ok=True)
    
    # --- ALTERAÇÃO AQUI: Lista abrangente de extensões RAW (Ignora maiúsculas/minúsculas) ---
    extensoes = ['*.nef', '*.NEF', '*.arw', '*.ARW', '*.cr2', '*.CR2', '*.cr3', '*.CR3', '*.raf', '*.RAF', '*.dng', '*.DNG','*.rw2', '*.RW2', '*.orf', '*.ORF']
    arquivos_raw = []
    for ext in extensoes:
        arquivos_raw.extend(glob.glob(os.path.join(diretorio_fotos, ext)))
    
    # Remove duplicatas de busca caso o sistema operacional seja case-insensitive
    arquivos_raw = list(set(arquivos_raw))
    
    if not arquivos_raw:
        widget_texto_status.insert(tk.END, "Nenhum arquivo RAW suportado encontrado no diretório informado.\n")
        btn_trigger.config(state=tk.NORMAL)
        return

    widget_texto_status.insert(tk.END, f"Encontrados {len(arquivos_raw)} arquivos RAW para análise.\nIniciando triagem...\n")
    widget_texto_status.see(tk.END)

    for caminho_arquivo in arquivos_raw:
        nome_arquivo = os.path.basename(caminho_arquivo)
        excentricidade = analisar_excentricidade_nef(caminho_arquivo) # Mantido o nome original para segurança
        
        if excentricidade is None:
            linha = f"[-] {nome_arquivo}: Pulado (Estrelas centrais insuficientes).\n"
        elif excentricidade > threshold_limite:
            shutil.move(caminho_arquivo, os.path.join(pasta_ruins, nome_arquivo))
            linha = f"[X] {nome_arquivo}: REJEITADO (Excentricidade: {excentricidade:.3f})\n"
        else:
            shutil.move(caminho_arquivo, os.path.join(pasta_boas, nome_arquivo))
            linha = f"[O] {nome_arquivo}: APROVADO  (Excentricidade: {excentricidade:.3f})\n"
            
        widget_texto_status.insert(tk.END, linha)
        widget_texto_status.see(tk.END)

    widget_texto_status.insert(tk.END, "\nTriagem concluída com sucesso!\n")
    widget_texto_status.see(tk.END)
    btn_trigger.config(state=tk.NORMAL)
    messagebox.showinfo("Sucesso", "A triagem das fotos foi finalizada!")

# --- FUNÇÕES AUXILIARES DA INTERFACE ---
def selecionar_diretorio():
    pasta_selecionada = filedialog.askdirectory()
    if pasta_selecionada:
        entry_diretorio.delete(0, tk.END)
        entry_diretorio.insert(0, pasta_selecionada)

def iniciar_processamento():
    diretorio = entry_diretorio.get().strip()
    limite_str = entry_limite.get().strip()
    
    if not diretorio or not os.path.exists(diretorio):
        messagebox.showerror("Erro", "Por favor, selecione um diretório válido.")
        return
        
    try:
        limite = float(limite_str)
    except ValueError:
        messagebox.showerror("Erro", "O limite de excentricidade deve ser um número decimal válido (ex: 0.520).")
        return
        
    txt_status.delete(1.0, tk.END)
    btn_aplicar.config(state=tk.DISABLED)
    
    # Roda em segundo plano (Thread) para a janela autônoma NUNCA congelar no Windows
    threading.Thread(target=executar_triagem, args=(diretorio, limite, txt_status, root, btn_aplicar), daemon=True).start()

# --- CONSTRUÇÃO DA INTERFACE GRÁFICA AUTÔNOMA ---
if __name__ == "__main__":
    root = tk.Tk()
    root.title("RAW Astroculling")
    root.geometry("620x450")
    root.minsize(550, 350)

      # --- COLOCA O LOGO NA JANELA DE FORMA PURA (SEM ADICIONAR FUNÇÕES) ---
    # O Tkinter busca o arquivo .ico na mesma pasta onde o seu executável estiver rodando
    try:
        if os.path.exists("logo.ico"):
            root.iconbitmap("logo.ico")
    except Exception:
        pass
    
    frame = ttk.Frame(root, padding="15")
    frame.pack(fill=tk.BOTH, expand=True)
    
    # Campo 1: Diretório Alvo
    ttk.Label(frame, text="Diretório Alvo com fotos RAW:").pack(anchor=tk.W, pady=(0, 2))
    frame_dir = ttk.Frame(frame)
    frame_dir.pack(fill=tk.X, pady=(0, 10))
    
    entry_diretorio = ttk.Entry(frame_dir)
    entry_diretorio.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
    
    btn_procurar = ttk.Button(frame_dir, text="Procurar...", command=selecionar_diretorio)
    btn_procurar.pack(side=tk.RIGHT)
    
    # Campo 2: Limite Excentricidade
    ttk.Label(frame, text="Limite de Excentricidade:").pack(anchor=tk.W, pady=(0, 2))
    entry_limite = ttk.Entry(frame, width=15)
    entry_limite.pack(anchor=tk.W, pady=(0, 15))
    entry_limite.insert(0, "0.520")
    
    # Botão Aplicar
    btn_aplicar = ttk.Button(frame, text="Aplicar Triagem", command=iniciar_processamento)
    btn_aplicar.pack(fill=tk.X, pady=(0, 15))
    
    # Logs
    ttk.Label(frame, text="Progresso:").pack(anchor=tk.W, pady=(0, 2))
    txt_status = tk.Text(frame, height=10, wrap=tk.WORD, background="#f4f4f4")
    txt_status.pack(fill=tk.BOTH, expand=True)
    
    root.mainloop()
