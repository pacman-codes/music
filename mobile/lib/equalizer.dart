import 'dart:async';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'api.dart';
import 'player.dart';

const androidEqBootKey='lain_android_eq_opt_in';

double presetGain(String preset,double frequency){
  return switch(preset){
    'bass'=>frequency<250?3.0:frequency>4000?-1.0:0.0,
    'voice'=>frequency<250?-2.0:frequency>=500&&frequency<=4000?2.0:0.0,
    'treble'=>frequency>3000?3.0:frequency<250?-1.0:0.0,
    _=>0.0,
  };
}

class EqualizerPage extends StatefulWidget {
  final LainPlayer player;
  const EqualizerPage({super.key,required this.player});
  @override State<EqualizerPage> createState()=>_EqualizerPageState();
}
class _EqualizerPageState extends State<EqualizerPage>{
  AndroidEqualizer? get eq=>widget.player.equalizer;
  AndroidEqualizerParameters? params;
  List<double> values=[];
  bool boot=false,busy=false,enabled=false;
  String? status;
  @override void initState(){super.initState();unawaited(read());}
  Future<void> read()async{
    try{
      final opt=await Sessions.storage.read(key:androidEqBootKey)=='true';
      if(mounted)setState(()=>boot=opt);
    }catch(_){}
    await load();
  }
  Future<void> load()async{
    if(eq==null||busy)return;
    setState((){busy=true;status=null;});
    try{
      if(widget.player.engine.processingState==ProcessingState.idle)throw StateError('No audio session');
      final p=await eq!.parameters.timeout(const Duration(seconds:5));
      if(p.bands.isEmpty||p.minDecibels>=p.maxDecibels)throw StateError('Unsupported');
      if(mounted)setState((){params=p;values=p.bands.map((b)=>b.gain).toList();enabled=eq!.enabled;});
    }catch(_){if(mounted)setState(()=>status='Сначала включи трек. Если полосы не появились, устройство не поддерживает этот эффект.');}
    finally{if(mounted)setState(()=>busy=false);}
  }
  Future<void> changeBoot(bool value)async{
    try{
      await Sessions.storage.write(key:androidEqBootKey,value:value?'true':'false');
      if(!value&&eq!=null){try{await eq!.setEnabled(false);}catch(_){}}
      if(mounted)setState((){boot=value;if(!value)enabled=false;status='Настройка подключения эффекта вступит в силу после перезапуска приложения.';});
    }catch(_){if(mounted)setState(()=>status='Не удалось сохранить настройку');}
  }
  Future<void> action(Future<void> Function() work)async{
    if(busy)return;
    setState((){busy=true;status=null;});
    try{await work();}
    catch(_){if(mounted)setState(()=>status='Эффект недоступен на текущем аудиоустройстве.');}
    finally{if(mounted)setState(()=>busy=false);}
  }
  double minGain(AndroidEqualizerParameters p)=>p.minDecibels.clamp(-6,0).toDouble();
  double maxGain(AndroidEqualizerParameters p)=>p.maxDecibels.clamp(0,6).toDouble();
  Future<void> applyPreset(String name)=>action(()async{
    final p=params;
    if(p==null)return;
    for(var i=0;i<p.bands.length;i++){
      final value=presetGain(name,p.bands[i].centerFrequency).clamp(minGain(p),maxGain(p)).toDouble();
      await p.bands[i].setGain(value);
      if(mounted)setState(()=>values[i]=value);
    }
  });
  String frequency(double hz)=>hz>=1000?'${(hz/1000).toStringAsFixed(1)} кГц':'${hz.round()} Гц';
  @override Widget build(BuildContext context){
    final p=params;
    return Scaffold(appBar:AppBar(title:const Text('Эквалайзер Android'),actions:[
      IconButton(onPressed:busy?null:load,icon:const Icon(Icons.refresh)),
    ]),body:ListView(padding:const EdgeInsets.all(16),children:[
      SwitchListTile(title:const Text('Подключать аудиоэффект'),subtitle:const Text('Экспериментально. Для изменения подключения перезапусти приложение.'),
        value:boot,onChanged:busy?null:changeBoot),
      if(eq==null)const Text('Эффект не подключён к текущему плееру. Включи настройку и перезапусти LAIN.'),
      if(p!=null)...[
        SwitchListTile(title:const Text('Эквалайзер включён'),value:enabled,onChanged:busy||!boot?null:(value)=>action(()async{
          await eq!.setEnabled(value);if(mounted)setState(()=>enabled=value);
        })),
        Wrap(spacing:8,children:[
          for(final item in const {'flat':'Ровно','bass':'Бас','voice':'Вокал','treble':'Высокие'}.entries)
            OutlinedButton(onPressed:busy?null:()=>applyPreset(item.key),child:Text(item.value)),
        ]),
        for(var i=0;i<p.bands.length;i++)band(p,i),
        const Text('Количество полос определяется Android. Усиление ограничено ±6 дБ и возможностями устройства. Положительные значения могут вызвать перегрузку; при искажениях снизь усиление и громкость.'),
        const Text('Настройки полос действуют в текущем сеансе. Оригинальные файлы не меняются.'),
      ],
      if(busy)const LinearProgressIndicator(),
      if(status!=null)Text(status!),
    ]));
  }
  Widget band(AndroidEqualizerParameters p,int i){
    final low=minGain(p),high=maxGain(p);
    final value=values[i].clamp(low,high).toDouble();
    return Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
      Text('${frequency(p.bands[i].centerFrequency)} · ${value.toStringAsFixed(1)} дБ'),
      Slider(value:value,min:low,max:high>low?high:low+1,
        onChanged:busy||high<=low?null:(v)=>setState(()=>values[i]=v),
        onChangeEnd:busy||high<=low?null:(v)=>action(()async{await p.bands[i].setGain(v);}),
      ),
    ]);
  }
}
