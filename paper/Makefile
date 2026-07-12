# Build the HardwareX draft. `make` -> main.pdf ; `make clean` removes aux files.
main.pdf: main.tex refs.bib
	pdflatex -interaction=nonstopmode main.tex
	-bibtex main
	pdflatex -interaction=nonstopmode main.tex
	pdflatex -interaction=nonstopmode main.tex

.PHONY: clean
clean:
	rm -f main.aux main.log main.out main.toc main.bbl main.blg
