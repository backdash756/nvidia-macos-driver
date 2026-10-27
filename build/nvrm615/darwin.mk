# Adapted from watchthelight-labs/NvGfx, build/nvgfx/nvrm/darwin.mk,
# commit 0eff8317443f7af55dc4b4fd333173819859799c (watchthelight).
# License: PolyForm-Noncommercial-1.0.0; see the repository LICENSE.
# Preserve NVIDIA's source/object rules, replacing only its ELF final link with
# Mach-O static archives. The existing NullMoth shader glue supplies the blobs.
# 615's Linux return thunk has no XNU provider; NvGfx drops this flag too.
CFLAGS := $(filter-out -mfunction-return=thunk-extern,$(CFLAGS))

NVRM615_LIB ?= libnvkernel.a
NVRM615_OBJS = $(filter-out $(SHADER_OBJS),$(OBJS))

.PHONY: reference-objs reference-archive reference-print-objs
reference-objs: $(NVRM615_OBJS)
reference-archive: $(OUTPUTDIR)/$(NVRM615_LIB)
reference-print-objs:
	@printf '%s\n' $(NVRM615_OBJS)

$(OUTPUTDIR)/$(NVRM615_LIB): $(NVRM615_OBJS)
	@$(RM) $@
	@printf '%s\n' $(NVRM615_OBJS) > $@.filelist
	$(at_if_quiet)$(NVRM615_LIBTOOL) -static -no_warning_for_no_symbols -o $@ -filelist $@.filelist
